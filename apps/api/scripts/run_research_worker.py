from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.db import create_database_engine
from app.observability import configure_sentry
from app.workers.research_jobs import ResearchJobWorker


async def main() -> None:
    settings = get_settings()
    configure_sentry(settings, service="research-worker")
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for the research worker")
    engine = create_database_engine(settings.database_url)
    try:
        await ResearchJobWorker(engine, settings).run_forever()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
