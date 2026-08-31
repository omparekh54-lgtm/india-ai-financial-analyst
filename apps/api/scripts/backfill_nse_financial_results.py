from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from lxml import etree
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.http_fetcher import SourceFetchError
from app.connectors.nse_financial_results import NseFinancialResultsFetcher
from app.connectors.nse_xbrl import NseFinancialXbrlFetcher
from app.core.agent_data_readiness import load_agent_data_coverage
from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.exchange import ExchangeDisclosure, ExchangeDisclosureIngestor
from app.ingestion.financials import FinancialFactIngestor
from app.ingestion.nse_financial_corpus import (
    financial_result_headline,
    financial_result_metadata,
    select_financial_result_records,
)
from app.ingestion.reference_provenance import resolve_security
from app.ingestion.xbrl_evidence import XbrlEvidenceIngestor
from app.ingestion.xbrl_financials import parse_financial_xbrl

API_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FinancialTarget:
    security_id: UUID
    symbol: str
    legal_name: str


async def _target_for_identifier(engine: AsyncEngine, identifier: str) -> FinancialTarget:
    security_id, legal_name = await resolve_security(engine, identifier)
    async with engine.connect() as connection:
        nse_symbol = await connection.scalar(
            text("select nse_symbol from securities where id = :security_id"),
            {"security_id": security_id},
        )
    symbol = str(nse_symbol or "").strip().upper()
    if not symbol:
        raise ValueError(f"security has no NSE symbol: {identifier}")
    return FinancialTarget(
        security_id=security_id,
        symbol=symbol,
        legal_name=legal_name,
    )


async def _all_targets(
    engine: AsyncEngine,
    *,
    limit: int,
    after_symbol: str | None,
    refresh_all: bool,
) -> list[FinancialTarget]:
    statement = text(
        """
        with nse_eq as (
          select id, nse_symbol, legal_name
          from securities
          where primary_exchange = 'NSE'
            and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
            and nse_symbol is not null
        ), financial_ready as (
          select ff.security_id
          from financial_facts ff
          join nse_eq n on n.id = ff.security_id
          where ff.source_id is not null
          group by ff.security_id
          having count(distinct ff.period_end) >= 8
             and count(distinct ff.fact_name) >= 6
        ), filing_ready as (
          select distinct src.security_id
          from sources src
          join nse_eq n on n.id = src.security_id
          join evidence_chunks ec on ec.source_id = src.id
          where src.source_type in ('exchange_filing', 'company_filing', 'regulator')
            and length(btrim(ec.content)) > 0
            and coalesce(src.published_at, src.retrieved_at) >= now() - interval '400 days'
        ), earnings_ready as (
          select distinct ce.security_id
          from corporate_events ce
          join nse_eq n on n.id = ce.security_id
          join corporate_event_sources ces on ces.event_id = ce.id
          join evidence_chunks ec on ec.source_id = ces.source_id
          join sources src on src.id = ces.source_id
          where ces.parse_status = 'parsed'
            and length(btrim(ec.content)) > 0
            and (
              ce.event_type in (
                'financial_results', 'earnings_call', 'earnings_transcript',
                'investor_presentation'
              )
              or ces.document_role in ('transcript', 'presentation', 'xbrl')
            )
            and coalesce(src.published_at, ce.event_at, src.retrieved_at)
                >= now() - interval '220 days'
        )
        select n.id, n.nse_symbol, n.legal_name
        from nse_eq n
        left join financial_ready f on f.security_id = n.id
        left join filing_ready fi on fi.security_id = n.id
        left join earnings_ready e on e.security_id = n.id
        where (:after_symbol is null or n.nse_symbol > :after_symbol)
          and (
            :refresh_all
            or f.security_id is null
            or fi.security_id is null
            or e.security_id is null
          )
        order by n.nse_symbol
        limit :limit
        """
    )
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                statement,
                {
                    "after_symbol": after_symbol.upper() if after_symbol else None,
                    "refresh_all": refresh_all,
                    "limit": limit,
                },
            )
        ).mappings().all()
    return [
        FinancialTarget(
            security_id=UUID(str(row["id"])),
            symbol=str(row["nse_symbol"]).strip().upper(),
            legal_name=str(row["legal_name"]),
        )
        for row in rows
    ]


async def _coverage(engine: AsyncEngine) -> dict[str, object]:
    coverage = await load_agent_data_coverage(engine)
    return {
        "nse_eq_securities": coverage.nse_eq_securities,
        "financial_history_securities": coverage.financial_history_securities,
        "recent_filing_evidence_securities": coverage.recent_filing_evidence_securities,
        "recent_earnings_evidence_securities": coverage.recent_earnings_evidence_securities,
    }


async def _process_target(
    *,
    engine: AsyncEngine,
    target: FinancialTarget,
    results_fetcher: NseFinancialResultsFetcher,
    xbrl_fetcher: NseFinancialXbrlFetcher,
    max_periods: int,
    min_selected_periods: int,
    document_delay_seconds: float,
    dry_run: bool,
) -> dict[str, object]:
    records = await results_fetcher.fetch_history(target.symbol)
    selected = select_financial_result_records(records, max_periods=max_periods)
    if len(selected) < min_selected_periods:
        raise ValueError(
            f"{target.symbol} exposes only {len(selected)} distinct NSE XBRL result periods; "
            f"minimum required is {min_selected_periods}"
        )

    if dry_run:
        return {
            "symbol": target.symbol,
            "legal_name": target.legal_name,
            "status": "dry_run",
            "selected_periods": [
                {
                    "period_end": item.record.period_end.isoformat()
                    if item.record.period_end
                    else None,
                    "period": item.record.period,
                    "xbrl_url": item.record.xbrl_url,
                    "timestamp_basis": item.timestamp_basis,
                }
                for item in selected
            ],
        }

    event_ingestor = ExchangeDisclosureIngestor(engine)
    fact_ingestor = FinancialFactIngestor(engine)
    evidence_ingestor = XbrlEvidenceIngestor(engine)
    documents: list[dict[str, object]] = []

    for position, item in enumerate(selected):
        fetched = await xbrl_fetcher.fetch(item.record.xbrl_url)
        facts = parse_financial_xbrl(fetched.content, fetched.media_type)
        if not facts:
            raise ValueError(
                f"{target.symbol} XBRL produced no numeric facts: {fetched.source_url}"
            )

        metadata = financial_result_metadata(item)
        disclosure = await event_ingestor.ingest(
            ExchangeDisclosure(
                security_id=target.security_id,
                exchange="NSE",
                source_uri=fetched.source_url,
                headline=financial_result_headline(item.record),
                published_at=item.published_at,
                title=(
                    f"{target.legal_name} financial results XBRL - "
                    f"{item.record.period_end.isoformat() if item.record.period_end else 'unknown'}"
                ),
                excerpt=(
                    f"Official NSE {item.record.period} XBRL financial results for "
                    f"{target.symbol}."
                ),
                metadata=metadata,
            )
        )
        if disclosure.event_type != "financial_results":
            raise RuntimeError(
                f"financial-result disclosure classified unexpectedly as {disclosure.event_type}"
            )

        financial_ingestion = await fact_ingestor.ingest_batch(
            security_id=target.security_id,
            source_id=disclosure.source_id,
            facts=facts,
        )
        evidence_ingestion = await evidence_ingestor.ingest(
            source_id=disclosure.source_id,
            event_id=disclosure.event_id,
            facts=facts,
            document_checksum=fetched.sha256,
            media_type=fetched.media_type,
        )
        documents.append(
            {
                "period_end": item.record.period_end.isoformat()
                if item.record.period_end
                else None,
                "source_id": str(disclosure.source_id),
                "event_id": str(disclosure.event_id),
                "xbrl_url": fetched.source_url,
                "document_sha256": fetched.sha256,
                "raw_fact_count": len(facts),
                "financial_ingestion": financial_ingestion,
                "evidence_ingestion": evidence_ingestion,
            }
        )
        if position + 1 < len(selected) and document_delay_seconds:
            await asyncio.sleep(document_delay_seconds)

    return {
        "symbol": target.symbol,
        "legal_name": target.legal_name,
        "status": "completed",
        "selected_period_count": len(selected),
        "documents": documents,
    }


async def _run() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill official NSE financial-result XBRL across one or a bounded batch of NSE "
            "equities. Each document feeds one provenance source, financial facts, a financial-"
            "results event and deterministic citable XBRL evidence."
        )
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--security", action="append", help="NSE symbol/BSE code/ISIN")
    target_group.add_argument("--all", action="store_true", help="Process a bounded NSE batch")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--after-symbol")
    parser.add_argument("--max-periods", type=int, default=10)
    parser.add_argument("--min-selected-periods", type=int, default=8)
    parser.add_argument("--request-delay-seconds", type=float, default=0.35)
    parser.add_argument("--document-delay-seconds", type=float, default=0.10)
    parser.add_argument("--refresh-all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")
    if args.max_periods < 1 or args.max_periods > 20:
        parser.error("--max-periods must be between 1 and 20")
    if args.min_selected_periods < 1 or args.min_selected_periods > args.max_periods:
        parser.error("--min-selected-periods must be between 1 and --max-periods")
    if args.request_delay_seconds < 0 or args.request_delay_seconds > 10:
        parser.error("--request-delay-seconds must be between 0 and 10")
    if args.document_delay_seconds < 0 or args.document_delay_seconds > 10:
        parser.error("--document-delay-seconds must be between 0 and 10")
    if args.after_symbol and not args.all:
        parser.error("--after-symbol can only be used with --all")

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)

    try:
        if args.all:
            targets = await _all_targets(
                engine,
                limit=args.limit,
                after_symbol=args.after_symbol,
                refresh_all=args.refresh_all,
            )
        else:
            targets = [
                await _target_for_identifier(engine, identifier)
                for identifier in args.security or []
            ]

        before = await _coverage(engine)
        if not targets:
            print(
                json.dumps(
                    {
                        "status": "completed",
                        "target_count": 0,
                        "coverage_before": before,
                        "coverage_after": before,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        results: list[dict[str, object]] = []
        failure_count = 0
        async with NseFinancialResultsFetcher() as results_fetcher, NseFinancialXbrlFetcher() as xbrl_fetcher:
            for position, target in enumerate(targets):
                try:
                    result = await _process_target(
                        engine=engine,
                        target=target,
                        results_fetcher=results_fetcher,
                        xbrl_fetcher=xbrl_fetcher,
                        max_periods=args.max_periods,
                        min_selected_periods=args.min_selected_periods,
                        document_delay_seconds=args.document_delay_seconds,
                        dry_run=args.dry_run,
                    )
                    results.append(result)
                except (
                    SourceFetchError,
                    ValueError,
                    TypeError,
                    RuntimeError,
                    etree.XMLSyntaxError,
                ) as exc:
                    failure_count += 1
                    results.append(
                        {
                            "symbol": target.symbol,
                            "legal_name": target.legal_name,
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                if position + 1 < len(targets) and args.request_delay_seconds:
                    await asyncio.sleep(args.request_delay_seconds)

        after = before if args.dry_run else await _coverage(engine)
        output: dict[str, Any] = {
            "status": (
                "dry_run"
                if args.dry_run
                else "completed" if failure_count == 0 else "completed_with_failures"
            ),
            "provider": "NSE",
            "provenance_class": "official_source",
            "data_policy": "real_primary_xbrl_no_synthetic_fallback",
            "target_count": len(targets),
            "failure_count": failure_count,
            "next_after_symbol": targets[-1].symbol if args.all else None,
            "coverage_before": before,
            "coverage_after": after,
            "results": results,
        }
        print(json.dumps(output, indent=2, sort_keys=True, default=str))
        return 0 if failure_count == 0 else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
