from __future__ import annotations

import asyncio

import pytest

from app.core.async_ttl_cache import AsyncTTLCache


@pytest.mark.asyncio
async def test_cache_reuses_value_and_protects_it_from_mutation() -> None:
    cache: AsyncTTLCache[dict[str, object]] = AsyncTTLCache(60)
    calls = 0

    async def loader() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ready": False, "blocking_agents": ["financials"]}

    first, first_hit = await cache.get_or_load(loader)
    first["ready"] = True
    second, second_hit = await cache.get_or_load(loader)

    assert first_hit is False
    assert second_hit is True
    assert second["ready"] is False
    assert calls == 1


@pytest.mark.asyncio
async def test_cache_coalesces_concurrent_loads() -> None:
    cache: AsyncTTLCache[int] = AsyncTTLCache(60)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return 42

    results = await asyncio.gather(*(cache.get_or_load(loader) for _ in range(8)))

    assert [value for value, _ in results] == [42] * 8
    assert sum(1 for _, cache_hit in results if not cache_hit) == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_invalidate_forces_reload() -> None:
    cache: AsyncTTLCache[int] = AsyncTTLCache(60)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert await cache.get_or_load(loader) == (1, False)
    await cache.invalidate()
    assert await cache.get_or_load(loader) == (2, False)
