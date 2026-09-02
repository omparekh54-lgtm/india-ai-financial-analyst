from __future__ import annotations

from uuid import UUID

import httpx

from app.core.auth_isolation import verify_auth_isolation

JOB_ID = UUID("11111111-1111-4111-8111-111111111111")
OWNER_ID = "22222222-2222-4222-8222-222222222222"
OTHER_ID = "33333333-3333-4333-8333-333333333333"


def _transport(*, leak_job_to_other: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "auth isolation verifier must remain read-only"
        auth = request.headers.get("Authorization")
        path = request.url.path

        if path == "/v1/auth/me":
            if auth == "Bearer owner-token":
                return httpx.Response(200, json={"id": OWNER_ID, "email": None})
            if auth == "Bearer other-token":
                return httpx.Response(200, json={"id": OTHER_ID, "email": None})
            return httpx.Response(401, json={"detail": "invalid token"})

        if path == f"/v1/research/jobs/{JOB_ID}":
            if auth == "Bearer owner-token":
                return httpx.Response(200, json={"id": str(JOB_ID)})
            if auth == "Bearer other-token":
                return httpx.Response(
                    200 if leak_job_to_other else 404,
                    json={"id": str(JOB_ID)} if leak_job_to_other else {"detail": "not found"},
                )

        if path == "/v1/research/jobs" and auth == "Bearer other-token":
            jobs = [{"id": str(JOB_ID)}] if leak_job_to_other else []
            return httpx.Response(200, json={"count": len(jobs), "jobs": jobs})

        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_auth_isolation_passes_only_when_other_user_cannot_see_owner_job() -> None:
    async with httpx.AsyncClient(transport=_transport()) as client:
        report = await verify_auth_isolation(
            client,
            api_base_url="https://analyst.example",
            job_id=JOB_ID,
            owner_token="owner-token",
            other_token="other-token",
        )

    assert report.passed is True
    assert report.owner_authenticated is True
    assert report.other_authenticated is True
    assert report.distinct_users is True
    assert report.owner_can_read_job is True
    assert report.other_job_status == 404
    assert report.other_cannot_read_job is True
    assert report.other_job_absent_from_history is True


async def test_auth_isolation_fails_if_other_user_can_read_or_list_owner_job() -> None:
    async with httpx.AsyncClient(transport=_transport(leak_job_to_other=True)) as client:
        report = await verify_auth_isolation(
            client,
            api_base_url="https://analyst.example",
            job_id=JOB_ID,
            owner_token="owner-token",
            other_token="other-token",
        )

    assert report.passed is False
    assert report.other_job_status == 200
    assert report.other_cannot_read_job is False
    assert report.other_job_absent_from_history is False


async def test_auth_isolation_fails_when_tokens_resolve_to_same_user() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == "/v1/auth/me":
            return httpx.Response(200, json={"id": OWNER_ID})
        if request.url.path == f"/v1/research/jobs/{JOB_ID}":
            return httpx.Response(200, json={"id": str(JOB_ID)})
        if request.url.path == "/v1/research/jobs":
            return httpx.Response(200, json={"count": 1, "jobs": [{"id": str(JOB_ID)}]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await verify_auth_isolation(
            client,
            api_base_url="https://analyst.example",
            job_id=JOB_ID,
            owner_token="first-token",
            other_token="second-token",
        )

    assert report.passed is False
    assert report.distinct_users is False
