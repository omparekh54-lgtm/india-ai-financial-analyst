from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Freshness(StrEnum):
    LIVE = "live"
    NEAR_LIVE = "near_live"
    PERIODIC = "periodic"
    HISTORICAL = "historical"
    UNKNOWN = "unknown"


class SourceEnvelope(BaseModel):
    source_type: str
    source_uri: str
    title: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    freshness: Freshness = Freshness.UNKNOWN
    payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
