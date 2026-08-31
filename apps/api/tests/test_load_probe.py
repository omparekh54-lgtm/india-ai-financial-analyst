from __future__ import annotations

import httpx
import pytest

from app.core.load_probe import run_read_only_load_probe


@pytest.mark.asyncio
async def test_load_probe_runs_only_selected_get_endpoints() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_read_only_load_probe(
            client,
            api_base_url="https://api.example.com",
            endpoints=("/health", "/v1/system/agents"),
            request_count=8,
            concurrency=3,
        )

    assert report.passed is True
    assert report.success_count == 8
    assert report.failure_count == 0
    assert set(seen) == {("GET", "/health"), ("GET", "/v1/system/agents")}
    payload = report.as_dict()
    assert payload["status_counts"] == {"200": 8}
    latency = payload["latency_ms"]
    assert isinstance(latency, dict)
    assert latency["p50"] is not None
    assert latency["p95"] is not None


@pytest.mark.asyncio
async def test_load_probe_rejects_mutating_or_unknown_endpoint() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="not allow-listed"):
            await run_read_only_load_probe(
                client,
                api_base_url="https://api.example.com",
                endpoints=("/v1/research/run",),
                request_count=1,
                concurrency=1,
            )


@pytest.mark.asyncio
async def test_load_probe_requires_token_for_authenticated_endpoint() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="require an access token"):
            await run_read_only_load_probe(
                client,
                api_base_url="https://api.example.com",
                endpoints=("/v1/system/data-readiness",),
                request_count=1,
                concurrency=1,
            )


@pytest.mark.asyncio
async def test_load_probe_does_not_put_token_in_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.query == b""
        assert request.headers.get("authorization") == "Bearer secret-token"
        return httpx.Response(200, json={"ready": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_read_only_load_probe(
            client,
            api_base_url="https://api.example.com",
            endpoints=("/v1/system/data-readiness",),
            request_count=2,
            concurrency=1,
            access_token="secret-token",
        )
    assert report.passed is True


@pytest.mark.asyncio
async def test_load_probe_rejects_plain_http_when_token_is_present() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="require HTTPS"):
            await run_read_only_load_probe(
                client,
                api_base_url="http://api.example.com",
                endpoints=("/v1/auth/me",),
                request_count=1,
                concurrency=1,
                access_token="secret-token",
            )


@pytest.mark.asyncio
async def test_load_probe_allows_loopback_http_with_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer secret-token"
        return httpx.Response(200, json={"id": "user"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await run_read_only_load_probe(
            client,
            api_base_url="http://localhost:8000",
            endpoints=("/v1/auth/me",),
            request_count=1,
            concurrency=1,
            access_token="secret-token",
        )
    assert report.passed is True


@pytest.mark.asyncio
async def test_load_probe_rejects_credentialed_base_url_and_unsafe_limits() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        with pytest.raises(ValueError, match="embedded credentials"):
            await run_read_only_load_probe(
                client,
                api_base_url="https://user:pass@api.example.com",
                endpoints=("/health",),
                request_count=1,
                concurrency=1,
            )
        with pytest.raises(ValueError, match="request_count"):
            await run_read_only_load_probe(
                client,
                api_base_url="https://api.example.com",
                endpoints=("/health",),
                request_count=501,
                concurrency=1,
            )
