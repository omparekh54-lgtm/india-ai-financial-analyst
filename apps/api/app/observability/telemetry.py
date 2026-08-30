from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings


class ResearchTelemetry:
    """Best-effort server-side product telemetry with no research content or secrets."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.posthog_key and self.settings.posthog_host)

    async def capture(
        self,
        event: str,
        *,
        user_id: UUID | None,
        properties: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "api_key": self.settings.posthog_key,
            "event": event,
            "properties": {
                "distinct_id": _anonymous_distinct_id(user_id),
                "$process_person_profile": False,
                "environment": self.settings.app_env,
                **_safe_properties(properties or {}),
            },
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.5)) as client:
                response = await client.post(
                    f"{self.settings.posthog_host.rstrip('/')}/capture/",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError:
            # Analytics must never affect the research path.
            return


def _anonymous_distinct_id(user_id: UUID | None) -> str:
    if user_id is None:
        return "anonymous-backend"
    digest = hashlib.sha256(f"research-user:{user_id}".encode()).hexdigest()
    return f"u_{digest[:24]}"


def _safe_properties(properties: dict[str, Any]) -> dict[str, Any]:
    allowed: dict[str, Any] = {}
    for key, value in properties.items():
        if key in {
            "mode",
            "status",
            "duration_ms",
            "claim_count",
            "agent_count",
            "data_confidence",
            "semantic_enabled",
            "multimodal_enabled",
            "audio_enabled",
            "live_market_enabled",
            "external_data_enabled",
            "external_llm_enabled",
        } and isinstance(value, (str, int, float, bool, type(None))):
            allowed[key] = value
    return allowed
