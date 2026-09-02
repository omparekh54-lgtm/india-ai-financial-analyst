from __future__ import annotations

import argparse
import asyncio
import json

from app.calibration import DEFAULT_BENCHMARK_CODE, CalibrationRepository
from app.core.config import get_settings
from app.db import create_database_engine


async def _run(limit: int, benchmark_code: str) -> dict[str, object]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        return await CalibrationRepository(engine).evaluate_due_snapshots(
            limit=limit,
            benchmark_code=benchmark_code,
        )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate mature research snapshots against future source-linked daily bars. "
            "This is a no-lookahead calibration ledger, not a trading backtest."
        )
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--benchmark-code", default=DEFAULT_BENCHMARK_CODE)
    args = parser.parse_args()
    if not 1 <= args.limit <= 500:
        parser.error("--limit must be between 1 and 500")
    try:
        result = asyncio.run(_run(args.limit, args.benchmark_code.strip().upper()))
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"status": "completed", **result}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
