from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import text

from app.core.agent_data_readiness import evaluate_agent_readiness, load_agent_data_coverage
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.core.financial_history_coverage import load_financial_history_coverage
from app.core.market_history_coverage import load_market_history_coverage
from app.core.peer_metric_coverage import load_peer_metric_coverage
from app.db import create_database_engine

_SECURITY_DIMENSION_QUERY = text(
    """
    with nse_eq as (
      select id, nse_symbol, legal_name, sector, industry, metadata
      from securities
      where primary_exchange = 'NSE'
        and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
    )
    select
      n.nse_symbol,
      n.legal_name,
      exists (
        select 1 from provider_instruments pi where pi.security_id = n.id
      ) as mapping_ready,
      (
        nullif(btrim(coalesce(n.sector, '')), '') is not null
        and nullif(btrim(coalesce(n.industry, '')), '') is not null
        and n.metadata->>'classification_taxonomy' = 'NSE_INDICES_4_TIER'
        and n.metadata->>'classification_provenance_class' = 'official_source'
        and n.metadata->>'classification_source_type' = 'nse_industry_classification'
        and nullif(btrim(coalesce(n.metadata->>'classification_sha256', '')), '') is not null
        and exists (
          select 1
          from sources src
          where src.id::text = n.metadata->>'classification_source_id'
            and src.security_id = n.id
            and src.source_type = 'nse_industry_classification'
            and src.metadata->>'provenance_class' = 'official_source'
            and coalesce(src.metadata->>'production_approved', 'false') = 'true'
            and src.checksum = n.metadata->>'classification_sha256'
        )
      ) as classification_ready,
      exists (
        select 1
        from sources src
        join evidence_chunks ec on ec.source_id = src.id
        where src.security_id = n.id
          and src.source_type in ('exchange_filing', 'company_filing', 'regulator')
          and length(btrim(ec.content)) > 0
          and coalesce(src.published_at, src.retrieved_at) >= now() - interval '400 days'
      ) as filing_ready,
      exists (
        select 1
        from corporate_events ce
        join corporate_event_sources ces on ces.event_id = ce.id
        join evidence_chunks ec on ec.source_id = ces.source_id
        join sources src on src.id = ces.source_id
        where ce.security_id = n.id
          and ces.parse_status = 'parsed'
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
      ) as earnings_ready
    from nse_eq n
    order by n.nse_symbol nulls last, n.legal_name
    """
)


def _missing_preview(rows: list[dict[str, object]], key: str, limit: int) -> list[dict[str, object]]:
    return [
        {
            "nse_symbol": row.get("nse_symbol"),
            "legal_name": row.get("legal_name"),
        }
        for row in rows
        if not bool(row.get(key))
    ][:limit]


async def main(limit: int, min_nse_eq_securities: int) -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            raw_rows = (await connection.execute(_SECURITY_DIMENSION_QUERY)).mappings().all()
        rows = [dict(row) for row in raw_rows]
        corpus_coverage = await load_data_coverage(engine)
        agent_coverage = await load_agent_data_coverage(engine)
        financial = await load_financial_history_coverage(engine)
        market = await load_market_history_coverage(engine)
        peer = await load_peer_metric_coverage(engine)
    finally:
        await engine.dispose()

    corpus_report = evaluate_data_coverage(
        corpus_coverage,
        min_nse_eq_securities=min_nse_eq_securities,
    )
    agent_report = evaluate_agent_readiness(
        agent_coverage,
        corpus_coverage,
        settings,
        min_nse_eq_securities=min_nse_eq_securities,
    )
    total = len(rows)

    dimension_counts: dict[str, dict[str, object]] = {}
    for key in ("mapping_ready", "classification_ready", "filing_ready", "earnings_ready"):
        covered = sum(bool(row.get(key)) for row in rows)
        dimension_counts[key] = {
            "covered": covered,
            "missing": total - covered,
            "coverage_pct": round((covered / total) * 100.0, 2) if total else 0.0,
            "missing_securities": _missing_preview(rows, key, limit),
            "sample_truncated": total - covered > limit,
        }

    payload = {
        "ready": corpus_report.ready and agent_report.ready,
        "read_only": True,
        "synthetic_data_written": False,
        "policy": "authoritative_listing_age_and_provenance_aware_coverage",
        "minimum_nse_eq_securities": min_nse_eq_securities,
        "nse_eq_securities": total,
        "security_dimensions": dimension_counts,
        "financial_history": financial.as_dict(preview_limit=limit),
        "market_history": market.as_dict(preview_limit=limit),
        "peer_metrics": peer.as_dict(preview_limit=limit),
        "corpus": corpus_report.as_dict(),
        "agents": agent_report.as_dict(),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Read-only report of the exact real-data gaps blocking the authoritative 16-agent "
            "production readiness contract."
        )
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-nse-eq-securities", type=int, default=1000)
    args = parser.parse_args()
    if args.min_nse_eq_securities < 1:
        parser.error("--min-nse-eq-securities must be >= 1")
    raise SystemExit(
        asyncio.run(
            main(
                max(1, min(args.limit, 2000)),
                args.min_nse_eq_securities,
            )
        )
    )
