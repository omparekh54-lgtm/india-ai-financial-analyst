from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@dataclass(frozen=True)
class DeploymentSmokeReport:
    health_status: int
    health_ok: bool
    readiness_status: int
    runtime_ready: bool
    agents_status: int
    sixteen_roles_present: bool
    auth_status: int
    authenticated: bool
    data_readiness_status: int
    corpus_ready: bool
    zero_nonproduction_sources: bool
    zero_unapproved_enabled_feeds: bool
    require_corpus_ready: bool

    @property
    def passed(self) -> bool:
        base = bool(
            self.health_ok
            and self.runtime_ready
            and self.sixteen_roles_present
            and self.authenticated
            and self.data_readiness_status == 200
            and self.zero_nonproduction_sources
            and self.zero_unapproved_enabled_feeds
        )
        return base and (self.corpus_ready if self.require_corpus_ready else True)

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "health_status": self.health_status,
            "health_ok": self.health_ok,
            "readiness_status": self.readiness_status,
            "runtime_ready": self.runtime_ready,
            "agents_status": self.agents_status,
            "sixteen_roles_present": self.sixteen_roles_present,
            "auth_status": self.auth_status,
            "authenticated": self.authenticated,
            "data_readiness_status": self.data_readiness_status,
            "corpus_ready": self.corpus_ready,
            "zero_nonproduction_sources": self.zero_nonproduction_sources,
            "zero_unapproved_enabled_feeds": self.zero_unapproved_enabled_feeds,
            "require_corpus_ready": self.require_corpus_ready,
        }


async def verify_deployment_smoke(
    client: httpx.AsyncClient,
    *,
    api_base_url: str,
    access_token: str,
    require_corpus_ready: bool = False,
) -> DeploymentSmokeReport:
    """Run GET-only deployment checks without creating research or mutating data."""
    token = access_token.strip()
    if not token:
        raise ValueError("access token cannot be empty")
    base = _validate_authenticated_target(api_base_url)
    auth_headers = {"Authorization": f"Bearer {token}"}

    health = await client.get(f"{base}/health")
    ready = await client.get(f"{base}/ready")
    agents = await client.get(f"{base}/v1/system/agents")
    auth = await client.get(f"{base}/v1/auth/me", headers=auth_headers)
    data = await client.get(f"{base}/v1/system/data-readiness", headers=auth_headers)

    health_payload = _mapping(health)
    ready_payload = _mapping(ready)
    agents_payload = _mapping(agents)
    data_payload = _mapping(data)
    agents_value = agents_payload.get("agents")
    agent_roles = list(agents_value) if isinstance(agents_value, list) else []
    coverage_value = data_payload.get("coverage")
    coverage = dict(coverage_value) if isinstance(coverage_value, dict) else {}

    return DeploymentSmokeReport(
        health_status=health.status_code,
        health_ok=bool(
            health.status_code == 200
            and health_payload.get("status") == "ok"
            and health_payload.get("database_healthy") is not False
        ),
        readiness_status=ready.status_code,
        runtime_ready=bool(
            ready.status_code == 200
            and ready_payload.get("status") == "ready"
            and ready_payload.get("ready") is True
        ),
        agents_status=agents.status_code,
        sixteen_roles_present=bool(
            agents.status_code == 200
            and agents_payload.get("count") == 16
            and len(agent_roles) == 16
        ),
        auth_status=auth.status_code,
        authenticated=auth.status_code == 200,
        data_readiness_status=data.status_code,
        corpus_ready=bool(data.status_code == 200 and data_payload.get("ready") is True),
        zero_nonproduction_sources=bool(
            data.status_code == 200 and coverage.get("nonproduction_sources") == 0
        ),
        zero_unapproved_enabled_feeds=bool(
            data.status_code == 200 and coverage.get("enabled_unapproved_official_feeds") == 0
        ),
        require_corpus_ready=require_corpus_ready,
    )


def _validate_authenticated_target(value: str) -> str:
    cleaned = value.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("api_base_url must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("api_base_url must not contain embedded credentials")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("api_base_url must not contain a path, query, or fragment")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" and host not in _LOOPBACK_HOSTS:
        raise ValueError("authenticated deployment smoke requires HTTPS outside localhost")
    return cleaned


def _mapping(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}