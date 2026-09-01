from __future__ import annotations

import argparse
import asyncio
import json

from app.core.agent_data_readiness import evaluate_agent_readiness, load_agent_data_coverage
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine


def _exit_code(*, corpus_ready: bool, agents_ready: bool) -> int:
    return 0 if corpus_ready and agents_ready else 1


async def _run(*, min_nse_eq_securities: int) -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    engine = create_database_engine(settings.database_url)
    try:
        corpus_coverage = await load_data_coverage(engine)
        agent_coverage = await load_agent_data_coverage(engine)
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
    ready = corpus_report.ready and agent_report.ready
    payload = {
        "ready": ready,
        "read_only": True,
        "synthetic_data_written": False,
        "minimum_nse_eq_securities": min_nse_eq_securities,
        "corpus": corpus_report.as_dict(),
        "agents": agent_report.as_dict(),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return _exit_code(corpus_ready=corpus_report.ready, agents_ready=agent_report.ready)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the authoritative production data contract for the India-first 16-agent "
            "research engine. This command is read-only and exits non-zero unless every required "
            "real-data gate is satisfied."
        )
    )
    parser.add_argument("--min-nse-eq-securities", type=int, default=1000)
    args = parser.parse_args()
    if args.min_nse_eq_securities < 1:
        parser.error("--min-nse-eq-securities must be >= 1")
    return asyncio.run(_run(min_nse_eq_securities=args.min_nse_eq_securities))


if __name__ == "__main__":
    raise SystemExit(main())
