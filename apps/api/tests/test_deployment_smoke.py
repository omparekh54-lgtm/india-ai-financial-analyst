from __future__ import annotations

import httpx
import pytest

from app.core.deployment_smoke import verify_deployment_smoke


def _transport(*, corpus_ready: bool = True, nonproduction_sources: int = 0) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "database_healthy": True})
        if path == "/ready":
            return httpx.Response(200, json={"status": "ready", "ready": True})
        if path == "/v1/system/agents":
            return httpx.Response(200, json={"count": 16, "agents": [str(i) for i in range(16)]})
        if path == "/v1/auth/me":
            assert request.headers.get("authorization") == "Bearer test-token"
            return httpx.Response(200, json={"id": "user"})
        if path == "/v1/system/data-readiness":
            return httpx.Response(
                200,
                json={
                    "ready": corpus_ready,
                    "coverage": {
                        "nonproduction_sources": nonproduction_sources,
                        "enabled_unapproved_official_feeds": 0,
                    },
                },
            )
        raise AssertionError(f"unexpected path: {path}")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_deployment_smoke_passes_without_requiring_populated_corpus() -> None:
    async with httpx.AsyncClient(transport=_transport(corpus_ready=False)) as client:
        report = await verify_deployment_smoke(
            client,
            api_base_url="https://api.example.com",
            access_token="test-token",
        )
    assert report.passed is True
    assert report.corpus_ready is False


@pytest.mark.asyncio
async def test_deployment_smoke_can_require_corpus_ready() -> None:
    async with httpx.AsyncClient(transport=_transport(corpus_ready=False)) as client:
        report = await verify_deployment_smoke(
            client,
            api_base_url="https://api.example.com",
            access_token="test-token",
            require_corpus_ready=True,
        )
    assert report.passed is False


@pytest.mark.asyncio
async def test_deployment_smoke_rejects_nonproduction_corpus() -> None:
    async with httpx.AsyncClient(transport=_transport(nonproduction_sources=1)) as client:
        report = await verify_deployment_smoke(
            client,
            api_base_url="https://api.example.com",
            access_token="test-token",
        )
    assert report.passed is False
    assert report.zero_nonproduction_sources is False
