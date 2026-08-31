from __future__ import annotations

from dataclasses import dataclass

import httpx


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
    base = api_base_url.rstrip("/")
    token = access_token.strip()
    if not token:
        raise ValueError("access token cannot be empty")
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
    coverage = data_payload.get("coverage") if isinstance(data_payload.get("coverage"), dict) else {}

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
            and isinstance(agents_payload.get("agents"), list)
            and len(agents_payload["agents"]) == 16
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


def _mapping(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
