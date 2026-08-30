from __future__ import annotations

from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


@lru_cache(maxsize=4)
def create_database_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        normalize_database_url(database_url),
        pool_pre_ping=True,
        pool_recycle=300,
    )


async def database_health(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("select 1"))
        return True
    except Exception:  # noqa: BLE001 - health probes intentionally collapse DB failures to false
        return False
