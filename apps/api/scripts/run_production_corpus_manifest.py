from __future__ import annotations

import argparse
import asyncio
import json

from app.core.agent_data_readiness import evaluate_agent_readiness, load_agent_data_coverage
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine


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
    next_actions: list[dict[str, str]] = []
    if coverage.nse_eq_securities < min_nse_eq_securities:
        next_actions.append(
            {
                "stage": "market",
                "action": "Run bootstrap_production_market_corpus.py to import the genuine NSE EQ security master.",
            }
        )
    if coverage.provider_instruments < coverage.nse_eq_securities:
        next_actions.append(
            {
                "stage": "market",
                "action": "Backfill real provider instrument mappings for the full NSE EQ universe.",
            }
        )
    if coverage.nse_securities_with_market_bars < coverage.nse_eq_securities:
        next_actions.append(
            {
                "stage": "market",
                "action": "Backfill listing-aware source-linked daily market history for uncovered securities.",
            }
        )
    if coverage.nse_securities_with_financial_facts < coverage.nse_eq_securities:
        next_actions.append(
            {
                "stage": "financials",
                "action": "Run the authoritative NSE financial-results/XBRL backfill for uncovered securities.",
            }
        )
    if coverage.nse_securities_with_security_metrics < coverage.nse_eq_securities:
        next_actions.append(
            {
                "stage": "peer_metrics",
                "action": "Derive and persist source-backed comparable metrics after financial/market inputs are complete.",
            }
        )
    if coverage.evidence_chunks == 0:
        next_actions.append(
            {
                "stage": "evidence",
                "action": "Ingest and parse real primary filings/results into source-linked evidence chunks.",
            }
        )
    if coverage.sourced_benchmark_bars == 0:
        next_actions.append(
            {
                "stage": "market_context",
                "action": "Populate source-linked NIFTY 50 and India VIX benchmark history.",
            }
        )
    if coverage.sourced_macro_observations == 0:
        next_actions.append(
            {
                "stage": "market_context",
                "action": "Populate the required India/global macro series from approved real sources.",
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
