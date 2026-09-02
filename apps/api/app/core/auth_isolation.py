from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import httpx


@dataclass(frozen=True)
class AuthIsolationReport:
    owner_authenticated: bool
    other_authenticated: bool
    distinct_users: bool
    owner_can_read_job: bool
    other_job_status: int
    other_cannot_read_job: bool
    other_job_absent_from_history: bool

    @property
    def passed(self) -> bool:
        return bool(
            self.owner_authenticated
            and self.other_authenticated
            and self.distinct_users
            and self.owner_can_read_job
            and self.other_cannot_read_job
            and self.other_job_absent_from_history
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "owner_authenticated": self.owner_authenticated,
            "other_authenticated": self.other_authenticated,
            "distinct_users": self.distinct_users,
            "owner_can_read_job": self.owner_can_read_job,
            "other_job_status": self.other_job_status,
            "other_cannot_read_job": self.other_cannot_read_job,
            "other_job_absent_from_history": self.other_job_absent_from_history,
        }


async def verify_auth_isolation(
    client: httpx.AsyncClient,
    *,
    api_base_url: str,
    job_id: UUID,
    owner_token: str,
    other_token: str,
) -> AuthIsolationReport:
    """Verify ownership isolation using only GET requests against an existing real job."""
    base = api_base_url.rstrip("/")
    owner_headers = _auth_headers(owner_token)
    other_headers = _auth_headers(other_token)

    owner_me = await client.get(f"{base}/v1/auth/me", headers=owner_headers)
    other_me = await client.get(f"{base}/v1/auth/me", headers=other_headers)
    owner_id = _authenticated_user_id(owner_me)
    other_id = _authenticated_user_id(other_me)

    owner_job = await client.get(
        f"{base}/v1/research/jobs/{job_id}",
        headers=owner_headers,
    )
    other_job = await client.get(
        f"{base}/v1/research/jobs/{job_id}",
        headers=other_headers,
    )
    other_history = await client.get(
        f"{base}/v1/research/jobs",
        headers=other_headers,
        params={"limit": 100},
    )

    return AuthIsolationReport(
        owner_authenticated=owner_id is not None,
        other_authenticated=other_id is not None,
        distinct_users=bool(owner_id and other_id and owner_id != other_id),
        owner_can_read_job=owner_job.status_code == 200,
        other_job_status=other_job.status_code,
        other_cannot_read_job=other_job.status_code == 404,
        other_job_absent_from_history=_job_absent_from_history(other_history, job_id),
    )


def _auth_headers(token: str) -> dict[str, str]:
    value = token.strip()
    if not value:
        raise ValueError("access token cannot be empty")
    return {"Authorization": f"Bearer {value}"}


def _authenticated_user_id(response: httpx.Response) -> str | None:
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    user_id = payload.get("id")
    return str(user_id).strip() if user_id else None


def _job_absent_from_history(response: httpx.Response, job_id: UUID) -> bool:
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        return False
    expected = str(job_id)
    for item in payload["jobs"]:
        if isinstance(item, dict) and str(item.get("id") or "") == expected:
            return False
    return True
