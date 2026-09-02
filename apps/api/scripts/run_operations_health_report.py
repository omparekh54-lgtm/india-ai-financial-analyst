from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import get_settings
from app.core.operations_health import load_operations_health
from app.db import create_database_engine


async def _run(min_nse_eq_securities: int) -> dict[str, object]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        return (
            await load_operations_health(
                engine,
                settings,
                min_nse_eq_securities=min_nse_eq_securities,
            )
        ).as_dict()
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only operations health for corpus, agent readiness, research queue, ingestion "
            "workers, official feeds, evidence embeddings and calibration backlog."
        )
    )
    parser.add_argument("--min-nse-eq-securities", type=int, default=1000)
    args = parser.parse_args()
    if args.min_nse_eq_securities < 1000:
        parser.error("--min-nse-eq-securities must be >= 1000 for production")
    try:
        payload = asyncio.run(_run(args.min_nse_eq_securities))
    except RuntimeError as exc:
        print(json.dumps({"ready": False, "errors": [str(exc)]}, indent=2, sort_keys=True))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if payload.get("ready") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
