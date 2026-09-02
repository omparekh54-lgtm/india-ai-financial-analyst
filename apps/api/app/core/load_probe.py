from __future__ import annotations

import asyncio
import math
from collections import Counter
from dataclasses import dataclass
from time import perf_counter
from urllib.parse import urlparse

import httpx

SAFE_LOAD_PROBE_ENDPOINTS = frozenset(
    {
        "/health",
        "/ready",
        "/v1/system/agents",
        "/v1/auth/me",
        "/v1/system/data-readiness",
    }
)
_AUTH_REQUIRED_ENDPOINTS = frozenset({"/v1/auth/me", "/v1/system/data-readiness"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class LoadProbeSample:
    endpoint: str
    status_code: int | None
    latency_ms: float
    error_type: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.error_type is None and self.status_code is not None and 200 <= self.status_code < 400)


@dataclass(frozen=True)
class LoadProbeReport:
    request_count: int
    concurrency: int
    samples: tuple[LoadProbeSample, ...]

    @property
    def success_count(self) -> int:
        return sum(sample.ok for sample in self.samples)

    @property
    def failure_count(self) -> int:
        return len(self.samples) - self.success_count

    @property
    def passed(self) -> bool:
        return bool(len(self.samples) == self.request_count and self.failure_count == 0)

    def as_dict(self) -> dict[str, object]:
        latencies = [sample.latency_ms for sample in self.samples]
        statuses = Counter(
            str(sample.status_code) if sample.status_code is not None else "network_error"
            for sample in self.samples
        )
        errors = Counter(sample.error_type for sample in self.samples if sample.error_type)
        endpoint_counts = Counter(sample.endpoint for sample in self.samples)
        return {
            "passed": self.passed,
            "request_count": self.request_count,
            "concurrency": self.concurrency,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "status_counts": dict(sorted(statuses.items())),
            "error_counts": dict(sorted(errors.items())),
            "endpoint_counts": dict(sorted(endpoint_counts.items())),
            "latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "max": round(max(latencies), 3) if latencies else None,
            },
        }


async def run_read_only_load_probe(
    client: httpx.AsyncClient,
    *,
    api_base_url: str,
    endpoints: tuple[str, ...],
    request_count: int = 60,
    concurrency: int = 5,
    access_token: str | None = None,
) -> LoadProbeReport:
    """Exercise only allow-listed GET endpoints; never creates research or mutates data."""
    token = (access_token or "").strip()
    base = _validate_base_url(api_base_url, authenticated=bool(token))
    selected = _validate_endpoints(endpoints, access_token=token)
    if request_count < 1 or request_count > 500:
        raise ValueError("request_count must be between 1 and 500")
    if concurrency < 1 or concurrency > 25:
        raise ValueError("concurrency must be between 1 and 25")

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    semaphore = asyncio.Semaphore(concurrency)

    async def execute(index: int) -> LoadProbeSample:
        endpoint = selected[index % len(selected)]
        started = perf_counter()
        try:
            async with semaphore:
                response = await client.get(f"{base}{endpoint}", headers=headers)
            return LoadProbeSample(
                endpoint=endpoint,
                status_code=response.status_code,
                latency_ms=(perf_counter() - started) * 1000,
            )
        except httpx.HTTPError as exc:
            return LoadProbeSample(
                endpoint=endpoint,
                status_code=None,
                latency_ms=(perf_counter() - started) * 1000,
                error_type=type(exc).__name__,
            )

    samples = await asyncio.gather(*(execute(index) for index in range(request_count)))
    return LoadProbeReport(
        request_count=request_count,
        concurrency=concurrency,
        samples=tuple(samples),
    )


def _validate_base_url(value: str, *, authenticated: bool) -> str:
    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("api_base_url must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("api_base_url must not contain embedded credentials")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("api_base_url must not contain a path, query, or fragment")
    host = (parsed.hostname or "").lower()
    if authenticated and parsed.scheme != "https" and host not in _LOOPBACK_HOSTS:
        raise ValueError("authenticated load probes require HTTPS outside localhost")
    return cleaned


def _validate_endpoints(endpoints: tuple[str, ...], *, access_token: str | None) -> tuple[str, ...]:
    if not endpoints:
        raise ValueError("at least one endpoint is required")
    invalid = sorted(set(endpoints) - SAFE_LOAD_PROBE_ENDPOINTS)
    if invalid:
        raise ValueError("load probe endpoint is not allow-listed: " + ", ".join(invalid))
    token = (access_token or "").strip()
    protected = sorted(set(endpoints) & _AUTH_REQUIRED_ENDPOINTS)
    if protected and not token:
        raise ValueError("authenticated load probe endpoints require an access token")
    return endpoints


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 3)
