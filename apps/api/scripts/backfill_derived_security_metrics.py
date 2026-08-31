from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.db import create_database_engine
from app.ingestion.derived_metric_ingestion import DerivedSecurityMetricIngestor
from app.ingestion.derived_metrics import (
    PEER_USABLE_METRICS,
    MetricFinancialFact,
    MetricMarketClose,
    derive_peer_metrics,
)
from app.ingestion.reference_provenance import resolve_security

API_ROOT = Path(__file__).resolve().parents[1]


async def _target_ids(
    engine: AsyncEngine,
    *,
    identifiers: list[str] | None,
    limit: int,
    after_symbol: str | None,
    refresh_all: bool,
) -> list[tuple[UUID, str, str]]:
    if identifiers:
        targets: list[tuple[UUID, str, str]] = []
        async with engine.connect() as connection:
            for identifier in identifiers:
                security_id, legal_name = await resolve_security(engine, identifier)
                symbol = await connection.scalar(
                    text("select nse_symbol from securities where id = :security_id"),
                    {"security_id": security_id},
                )
                normalized = str(symbol or "").strip().upper()
                if not normalized:
                    raise ValueError(f"security has no NSE symbol: {identifier}")
                targets.append((security_id, normalized, legal_name))
        return targets

    statement = text(
        """
        with nse_eq as (
          select id, nse_symbol, legal_name
          from securities
          where primary_exchange = 'NSE'
            and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
            and nse_symbol is not null
        ), ready as (
          select sm.security_id
          from security_metrics sm
          join nse_eq n on n.id = sm.security_id
          where sm.source_id is not null
            and sm.as_of_date >= current_date - 400
            and sm.metric_name = any(:metric_names)
          group by sm.security_id
          having count(distinct sm.metric_name) >= 3
        )
        select n.id, n.nse_symbol, n.legal_name
        from nse_eq n
        left join ready r on r.security_id = n.id
        where (:after_symbol is null or n.nse_symbol > :after_symbol)
          and (:refresh_all or r.security_id is null)
        order by n.nse_symbol
        limit :limit
        """
    )
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                statement,
                {
                    "metric_names": sorted(PEER_USABLE_METRICS),
                    "after_symbol": after_symbol.upper() if after_symbol else None,
                    "refresh_all": refresh_all,
                    "limit": limit,
                },
            )
        ).mappings().all()
    return [
        (UUID(str(row["id"])), str(row["nse_symbol"]).upper(), str(row["legal_name"]))
        for row in rows
    ]


async def _facts(engine: AsyncEngine, security_id: UUID) -> list[MetricFinancialFact]:
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    with ranked as (
                      select ff.fact_name, ff.period_end, ff.period_type, ff.value, ff.unit,
                             ff.source_id,
                             row_number() over (
                               partition by ff.fact_name, ff.period_type
                               order by ff.period_end desc, ff.created_at desc
                             ) as rn
                      from financial_facts ff
                      where ff.security_id = :security_id
                        and ff.source_id is not null
                    )
                    select fact_name, period_end, period_type, value, unit, source_id
                    from ranked
                    where rn <= 8
                    order by fact_name, period_end desc
                    """
                ),
                {"security_id": security_id},
            )
        ).mappings().all()
    return [
        MetricFinancialFact(
            fact_name=str(row["fact_name"]),
            period_end=_date(row["period_end"]),
            period_type=str(row["period_type"]),
            value=Decimal(str(row["value"])),
            unit=str(row["unit"]) if row["unit"] is not None else None,
            source_id=UUID(str(row["source_id"])),
        )
        for row in rows
    ]


async def _market(engine: AsyncEngine, security_id: UUID) -> MetricMarketClose | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    select ts, close, source_id
                    from market_bars
                    where security_id = :security_id
                      and interval in ('1d', 'day', 'daily')
                      and source_id is not null
                      and close is not null
                    order by ts desc
                    limit 1
                    """
                ),
                {"security_id": security_id},
            )
        ).mappings().first()
    if row is None:
        return None
    ts = row["ts"]
    as_of_date = ts.date() if isinstance(ts, datetime) else _date(ts)
    return MetricMarketClose(
        as_of_date=as_of_date,
        price=Decimal(str(row["close"])),
        source_id=UUID(str(row["source_id"])),
    )


async def _ready_count(engine: AsyncEngine) -> tuple[int, int]:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    with nse_eq as (
                      select id from securities
                      where primary_exchange = 'NSE'
                        and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
                    ), ready as (
                      select sm.security_id
                      from security_metrics sm join nse_eq n on n.id = sm.security_id
                      where sm.source_id is not null
                        and sm.as_of_date >= current_date - 400
                        and sm.metric_name = any(:metric_names)
                      group by sm.security_id
                      having count(distinct sm.metric_name) >= 3
                    )
                    select
                      (select count(*) from nse_eq) as total,
                      (select count(*) from ready) as ready
                    """
                ),
                {"metric_names": sorted(PEER_USABLE_METRICS)},
            )
        ).mappings().one()
    return int(row["ready"] or 0), int(row["total"] or 0)


async def _run() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Derive peer/security metrics from source-linked normalized financial facts and "
            "stored market history. No external estimates or synthetic fallback are used."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--security", action="append")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--after-symbol")
    parser.add_argument("--min-metrics", type=int, default=3)
    parser.add_argument("--refresh-all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.limit <= 250:
        parser.error("--limit must be between 1 and 250")
    if not 1 <= args.min_metrics <= len(PEER_USABLE_METRICS):
        parser.error("--min-metrics is outside the supported metric range")
    if args.after_symbol and not args.all:
        parser.error("--after-symbol can only be used with --all")

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        targets = await _target_ids(
            engine,
            identifiers=args.security,
            limit=args.limit,
            after_symbol=args.after_symbol,
            refresh_all=args.refresh_all,
        )
        before_ready, before_total = await _ready_count(engine)
        ingestor = DerivedSecurityMetricIngestor(engine)
        results: list[dict[str, object]] = []
        failures = 0
        for security_id, symbol, legal_name in targets:
            try:
                facts = await _facts(engine, security_id)
                market = await _market(engine, security_id)
                bundle = derive_peer_metrics(facts, market=market)
                if len(bundle.metrics) < args.min_metrics:
                    raise ValueError(
                        f"{symbol} derives only {len(bundle.metrics)} peer-usable metrics; "
                        f"minimum required is {args.min_metrics}"
                    )
                result: dict[str, object] = {
                    "symbol": symbol,
                    "legal_name": legal_name,
                    "metric_count": len(bundle.metrics),
                    "metric_names": [item.metric_name for item in bundle.metrics],
                    "checksum": bundle.checksum,
                    "upstream_source_count": len(bundle.upstream_source_ids),
                }
                if args.dry_run:
                    result["status"] = "dry_run"
                else:
                    result["status"] = "completed"
                    result["ingestion"] = await ingestor.ingest(
                        security_id=security_id,
                        symbol=symbol,
                        bundle=bundle,
                    )
                results.append(result)
            except (ValueError, TypeError) as exc:
                failures += 1
                results.append(
                    {
                        "symbol": symbol,
                        "legal_name": legal_name,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )

        after_ready, after_total = (
            (before_ready, before_total) if args.dry_run else await _ready_count(engine)
        )
        print(
            json.dumps(
                {
                    "status": (
                        "dry_run"
                        if args.dry_run
                        else "completed" if failures == 0 else "completed_with_failures"
                    ),
                    "data_policy": "deterministic_source_linked_inputs_no_estimated_fallback",
                    "target_count": len(targets),
                    "failure_count": failures,
                    "next_after_symbol": targets[-1][1] if args.all and targets else None,
                    "ready_before": before_ready,
                    "universe_before": before_total,
                    "ready_after": after_ready,
                    "universe_after": after_total,
                    "results": results,
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        return 0 if failures == 0 else 1
    finally:
        await engine.dispose()


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
