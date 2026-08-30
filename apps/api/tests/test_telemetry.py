from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.telemetry import ProductTelemetry


@pytest.mark.asyncio
async def test_product_telemetry_is_opt_in_and_strips_sensitive_properties() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"status": 1})

    telemetry = ProductTelemetry(
        Settings(
            enable_product_telemetry=True,
            posthog_key="phc-test",
            posthog_host="https://posthog.example",
        ),
        transport=httpx.MockTransport(handler),
    )
    ok = await telemetry.capture(
        "research_completed",
        {
            "mode": "full_analysis",
            "claim_count": 42,
            "query": "SECRET USER QUERY",
            "email": "person@example.com",
            "broker_token": "secret",
            "source_content": "confidential document text",
        },
    )

    assert ok is True
    payload = captured["payload"]
    assert isinstance(payload, dict)
    properties = payload["properties"]  # type: ignore[index]
    assert properties["mode"] == "full_analysis"  # type: ignore[index]
    assert properties["claim_count"] == 42  # type: ignore[index]
    assert "query" not in properties
    assert "email" not in properties
    assert "broker_token" not in properties
    assert "source_content" not in properties
    assert captured["url"] == "https://posthog.example/capture/"


@pytest.mark.asyncio
async def test_product_telemetry_disabled_performs_no_request() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    telemetry = ProductTelemetry(
        Settings(enable_product_telemetry=False, posthog_key="phc-test"),
        transport=httpx.MockTransport(handler),
    )
    ok = await telemetry.capture("research_started", {"mode": "full_analysis"})

    assert ok is False
    assert called is False
