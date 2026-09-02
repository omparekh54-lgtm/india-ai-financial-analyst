from __future__ import annotations

import argparse
import asyncio
import json

from app.core.config import get_settings
from app.db import create_database_engine
from app.workers.official_feeds import OfficialFeedWorker


async def _run(limit: int) -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    engine = create_database_engine(settings.database_url)
    try:
        result = await OfficialFeedWorker(
            engine,
            external_data_enabled=settings.enable_external_data_calls,
            app_env=settings.app_env,
        ).run_once(limit=limit)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("failed_count", 0) == 0 and result.get("blocked_count", 0) == 0 else 1
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one leased pass of configured official India data feeds."
    )
    parser.add_argument("--limit", type=int, default=4, help="Maximum due feeds to claim")
    args = parser.parse_args()
    return asyncio.run(_run(max(1, min(args.limit, 20))))


if __name__ == "__main__":
    raise SystemExit(main())
