from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


class AsyncTTLCache(Generic[T]):
    """Small per-process async cache with request coalescing.

    Corpus readiness is expensive but changes only after ingestion jobs. Coalescing prevents
    concurrent dashboard requests from running the same cross-region aggregate queries.
    Values are copied on read so a response handler cannot mutate the cached object.
    """

    def __init__(self, ttl_seconds: float) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._value: T | None = None
        self._expires_at = 0.0

    async def get_or_load(self, loader: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        now = monotonic()
        if self._value is not None and now < self._expires_at:
            return deepcopy(self._value), True

        async with self._lock:
            now = monotonic()
            if self._value is not None and now < self._expires_at:
                return deepcopy(self._value), True
            value = await loader()
            self._value = deepcopy(value)
            self._expires_at = monotonic() + self._ttl_seconds
            return deepcopy(value), False

    async def invalidate(self) -> None:
        async with self._lock:
            self._value = None
            self._expires_at = 0.0
