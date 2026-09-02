from __future__ import annotations

import asyncio
import json

from app.core.agent_data_readiness import evaluate_agent_readiness, load_agent_data_coverage
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
                    "agent_readiness": {},
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    engine = create_database_engine(settings.database_url)
    try:
        coverage = await load_data_coverage(engine)
        agent_coverage = await load_agent_data_coverage(engine)
    finally:
        await engine.dispose()

    report = evaluate_data_coverage(coverage)
    agent_report = evaluate_agent_readiness(agent_coverage, coverage, settings)
    payload = report.as_dict()
    payload["agent_readiness"] = agent_report.as_dict()
    payload["blocking_agents"] = list(agent_report.blocking_agents)
    payload["ready"] = report.ready and agent_report.ready
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ready"] is True else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
