from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import text

from app.core.config import get_settings
from app.db import create_database_engine

_REQUIRED_MACRO_SERIES = (
    "repo_rate",
    "india_10y_yield",
    "usd_inr",
    "brent",
    "india_vix",
    "cpi_yoy",
    "iip_yoy",
    "fii_cash_net_cr",
    "dii_cash_net_cr",
)
_REQUIRED_BENCHMARK_CODES = ("NIFTY50", "INDIAVIX")

_GAP_QUERY = text(
    """
    with nse_eq as (
      select id, nse_symbol, legal_name, sector, industry
      from securities
      where primary_exchange = 'NSE'
        and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
    ), mapping_ok as (
      select distinct pi.security_id
      from provider_instruments pi
      join nse_eq n on n.id = pi.security_id
    ), financial_ok as (
      select ff.security_id
      from financial_facts ff
      join nse_eq n on n.id = ff.security_id
      where ff.source_id is not null
      group by ff.security_id
      having count(distinct ff.period_end) >= 8
         and count(distinct ff.fact_name) >= 6
    ), filing_ok as (
      select distinct src.security_id
      from sources src
      join nse_eq n on n.id = src.security_id
      join evidence_chunks ec on ec.source_id = src.id
      where src.source_type in ('exchange_filing', 'company_filing', 'regulator')
        and length(btrim(ec.content)) > 0
        and coalesce(src.published_at, src.retrieved_at) >= now() - interval '400 days'
    ), earnings_ok as (
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
    ), technical_ok as (
      select mb.security_id
      from market_bars mb
      join nse_eq n on n.id = mb.security_id
      where mb.source_id is not null
        and mb.interval in ('1d', 'day', 'daily')
        and mb.ts >= now() - interval '500 days'
      group by mb.security_id
      having count(distinct mb.ts::date) >= 200
    ), peer_ok as (
      select sm.security_id
      from security_metrics sm
      join nse_eq n on n.id = sm.security_id
      where sm.source_id is not null
        and sm.as_of_date >= current_date - 400
      group by sm.security_id
      having count(distinct sm.metric_name) >= 3
    )
    select
      n.id,
      n.nse_symbol,
      n.legal_name,
      case when m.security_id is null then false else true end as mapping_ready,
      case
        when nullif(btrim(coalesce(n.sector, '')), '') is not null
         and nullif(btrim(coalesce(n.industry, '')), '') is not null
        then true else false
      end as classification_ready,
      case when f.security_id is null then false else true end as financial_ready,
      case when fi.security_id is null then false else true end as filing_ready,
      case when e.security_id is null then false else true end as earnings_ready,
      case when t.security_id is null then false else true end as technical_ready,
      case when p.security_id is null then false else true end as peer_metrics_ready
    from nse_eq n
    left join mapping_ok m on m.security_id = n.id
    left join financial_ok f on f.security_id = n.id
    left join filing_ok fi on fi.security_id = n.id
    left join earnings_ok e on e.security_id = n.id
    left join technical_ok t on t.security_id = n.id
    left join peer_ok p on p.security_id = n.id
    order by n.nse_symbol nulls last, n.legal_name
    """
)


async def main(limit: int) -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(_GAP_QUERY)).mappings().all()
            benchmark_codes = {
                str(value).upper()
                for value in (
                    await connection.execute(
                        text(
                            """
                            select distinct b.code
                            from benchmark_bars bb
                            join benchmarks b on b.id = bb.benchmark_id
                            where bb.source_id is not null
                            """
                        )
                    )
                ).scalars().all()
            }
            macro_series = {
                str(value)
                for value in (
                    await connection.execute(
                        text(
                            """
                            select distinct series_key
                            from macro_observations
                            where source_id is not null
                            """
                        )
                    )
                ).scalars().all()
            }
    finally:
        await engine.dispose()

    dimensions = (
        "mapping_ready",
        "classification_ready",
        "financial_ready",
        "filing_ready",
        "earnings_ready",
        "technical_ready",
        "peer_metrics_ready",
    )
    total = len(rows)
    gaps: dict[str, object] = {}
    for dimension in dimensions:
        missing = [row for row in rows if not bool(row[dimension])]
        gaps[dimension] = {
            "covered": total - len(missing),
            "missing": len(missing),
            "coverage_pct": round(((total - len(missing)) / total) * 100, 2) if total else 0.0,
            "missing_securities": [
                {
                    "nse_symbol": row["nse_symbol"],
                    "legal_name": row["legal_name"],
                }
                for row in missing[:limit]
            ],
            "sample_truncated": len(missing) > limit,
        }

    payload = {
        "read_only": True,
        "synthetic_data_written": False,
        "nse_eq_securities": total,
        "security_gaps": gaps,
        "missing_benchmarks": sorted(set(_REQUIRED_BENCHMARK_CODES) - benchmark_codes),
        "missing_macro_series": sorted(set(_REQUIRED_MACRO_SERIES) - macro_series),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read-only report of the exact real-data gaps blocking agent readiness."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum missing securities to print per coverage dimension.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(max(1, min(args.limit, 2000)))))
