from __future__ import annotations

from collections.abc import Mapping

import httpx

from app.core.config import Settings

TelemetryValue = str | int | float | bool | None


class ProductTelemetry:
    """Best-effort, opt-in operational telemetry with a deliberately narrow property surface."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enable_product_telemetry and self.settings.posthog_key)

    async def capture(
        self,
        event: str,
        properties: Mapping[str, TelemetryValue] | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        event_name = event.strip()[:120]
        if not event_name:
            return False

        safe_properties: dict[str, TelemetryValue] = {
            "distinct_id": "financial-analyst-backend",
            "environment": self.settings.app_env[:80],
            "service": "india-ai-financial-analyst",
        }
        for key, value in (properties or {}).items():
            safe_key = str(key).strip()[:80]
            if not safe_key or safe_key in {"query", "email", "source_content", "broker_token"}:
                continue
            safe_properties[safe_key] = _safe_value(value)

        payload = {
            "api_key": self.settings.posthog_key,
            "event": event_name,
            "properties": safe_properties,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(2.0, connect=1.0),
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.settings.posthog_host.rstrip('/')}/capture/",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return False
        return True


def _safe_value(value: TelemetryValue) -> TelemetryValue:
    if isinstance(value, str):
        return value[:160]
    return value
