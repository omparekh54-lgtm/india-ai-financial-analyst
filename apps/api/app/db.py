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


# Engines are process-wide and their pools are meant to live for the whole process.
# Tracked separately from the lru_cache so shutdown can dispose them deterministically.
_ENGINES: dict[str, AsyncEngine] = {}


@lru_cache(maxsize=4)
def create_database_engine(database_url: str) -> AsyncEngine:
    """Return the process-wide engine for a database URL.

    Callers must NOT dispose the returned engine per request. Disposing tears down the pool
    of the shared cached engine, so the next caller receives the same object with an empty
    pool and pays a fresh TCP + TLS handshake. Dispose only at shutdown, via dispose_engines.
    """
    engine = create_async_engine(
        normalize_database_url(database_url),
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
    )
    _ENGINES[database_url] = engine
    return engine


async def dispose_engines() -> None:
    """Dispose every cached engine. Call once, on application shutdown."""
    for engine in list(_ENGINES.values()):
        await engine.dispose()
    _ENGINES.clear()
    create_database_engine.cache_clear()


async def database_health(engine: AsyncEngine) -> bool:
    """Research requires writes; a readable but read-only database is degraded."""
    try:
        async with engine.connect() as connection:
            mode = await connection.scalar(text("show transaction_read_only"))
        return mode == "off"
    except Exception:  # noqa: BLE001 - health probes intentionally collapse DB failures to false
        return False
