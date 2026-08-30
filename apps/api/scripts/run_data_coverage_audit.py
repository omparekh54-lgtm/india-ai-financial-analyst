from __future__ import annotations

import asyncio
import json

from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine


async def main() -> int:
    settings = get_settings()
    if not settings.database_url:
        print(
            json.dumps(
                {
                    "ready": False,
                    "errors": ["DATABASE_URL is not configured."],
                    "warnings": [],
                    "coverage": {},
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    engine = create_database_engine(settings.database_url)
    try:
        coverage = await load_data_coverage(engine)
    finally:
        await engine.dispose()

    report = evaluate_data_coverage(coverage)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
