from __future__ import annotations

import argparse
import asyncio
import json

from app.core.agent_data_readiness import evaluate_agent_readiness, load_agent_data_coverage
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine

_REQUIRED_BENCHMARKS = {"NIFTY50", "INDIAVIX"}
_REQUIRED_MACRO_SERIES = {
    "repo_rate",
    "india_10y_yield",
    "usd_inr",
    "brent",
    "india_vix",
    "cpi_yoy",
    "iip_yoy",
    "fii_cash_net_cr",
    "dii_cash_net_cr",
}


async def _run(min_nse_eq_securities: int) -> dict[str, object]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        coverage = await load_data_coverage(engine)
        agent_coverage = await load_agent_data_coverage(engine)
    finally:
        await engine.dispose()
    corpus = evaluate_data_coverage(
        coverage,
        min_nse_eq_securities=min_nse_eq_securities,
    )
    agents = evaluate_agent_readiness(agent_coverage, coverage, settings)
    total = agent_coverage.nse_eq_securities
    next_actions: list[dict[str, object]] = []

    def add(stage: str, action: str, covered: int | None = None) -> None:
        payload: dict[str, object] = {"stage": stage, "action": action}
        if covered is not None:
            payload["covered_securities"] = covered
            payload["required_securities"] = total
        next_actions.append(payload)

    if total < min_nse_eq_securities:
        add(
            "market",
            "Run bootstrap_production_market_corpus.py to import the genuine NSE EQ security master.",
            total,
        )
    if agent_coverage.provider_mapped_securities < total:
        add(
            "market",
            "Backfill exact real provider instrument mappings for every NSE EQ security.",
            agent_coverage.provider_mapped_securities,
        )
    if agent_coverage.classified_securities < total:
        add(
            "market",
            "Complete source-backed sector and industry classification for the NSE EQ universe.",
            agent_coverage.classified_securities,
        )
    if agent_coverage.technical_history_securities < total:
        add(
            "market",
            "Backfill listing-aware source-linked daily market history for uncovered securities.",
            agent_coverage.technical_history_securities,
        )
    if agent_coverage.financial_history_securities < total:
        add(
            "financials",
            "Run the authoritative NSE financial-results/XBRL history backfill for uncovered securities.",
            agent_coverage.financial_history_securities,
        )
    if agent_coverage.recent_filing_evidence_securities < total:
        add(
            "financials",
            "Ingest and parse recent primary filing evidence for uncovered securities.",
            agent_coverage.recent_filing_evidence_securities,
        )
    if agent_coverage.recent_earnings_evidence_securities < total:
        add(
            "financials",
            "Populate recent source-linked results/earnings evidence for uncovered securities.",
            agent_coverage.recent_earnings_evidence_securities,
        )
    if agent_coverage.peer_metric_securities < total:
        add(
            "peer_metrics",
            "Derive and persist the required source-backed comparable metrics after inputs are complete.",
            agent_coverage.peer_metric_securities,
        )

    missing_benchmarks = sorted(
        _REQUIRED_BENCHMARKS - set(agent_coverage.benchmark_codes_with_sourced_bars)
    )
    if missing_benchmarks:
        next_actions.append(
            {
                "stage": "market_context",
                "action": "Populate source-linked benchmark history for every required benchmark code.",
                "missing_benchmarks": missing_benchmarks,
            }
        )
    missing_macro = sorted(
        _REQUIRED_MACRO_SERIES - set(agent_coverage.macro_series_with_sourced_observations)
    )
    if missing_macro:
        next_actions.append(
            {
                "stage": "market_context",
                "action": "Populate the required India/global macro series from approved real sources.",
                "missing_macro_series": missing_macro,
            }
        )
    if coverage.evidence_chunks == 0:
        next_actions.append(
            {
                "stage": "evidence",
                "action": "Persist parsed source-linked evidence chunks; Agent 15 cannot validate an empty evidence store.",
            }
        )

    return {
        "ready": corpus.ready and agents.ready,
        "corpus": corpus.as_dict(),
        "agent_readiness": agents.as_dict(),
        "blocking_agents": list(agents.blocking_agents),
        "next_actions": next_actions,
        "production_policy": {
            "minimum_nse_eq_securities": min_nse_eq_securities,
            "synthetic_fallback_allowed": False,
            "provider_mapping_target_pct": 100,
            "classification_target_pct": 100,
            "financial_history_policy": "listing_aware",
            "technical_history_policy": "listing_aware",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Phase 25 production corpus gap manifest. No data is generated or mutated."
    )
    parser.add_argument("--min-nse-eq-securities", type=int, default=1000)
    args = parser.parse_args()
    if args.min_nse_eq_securities < 1000:
        parser.error("--min-nse-eq-securities must be >= 1000 in production")
    try:
        payload = asyncio.run(_run(args.min_nse_eq_securities))
    except RuntimeError as exc:
        print(json.dumps({"ready": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if payload.get("ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
