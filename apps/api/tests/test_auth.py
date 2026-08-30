from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.auth import SupabaseAuthVerifier, authenticated_user_from_payload
from app.core.config import Settings
from app.main import ResearchRunRequest


@pytest.mark.asyncio
async def test_supabase_auth_verifier_accepts_valid_user() -> None:
    user_id = "4f988990-b0c7-4c1f-8ec6-8021e7f4c943"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://project.supabase.co/auth/v1/user"
        assert request.headers["authorization"] == "Bearer valid-token"
        assert request.headers["apikey"] == "publishable-key"
        return httpx.Response(200, json={"id": user_id, "email": "analyst@example.com"})

    verifier = SupabaseAuthVerifier(
        Settings(
            supabase_url="https://project.supabase.co",
            supabase_publishable_key="publishable-key",
        ),
        transport=httpx.MockTransport(handler),
    )

    user = await verifier.verify("valid-token")

    assert user.id == UUID(user_id)
    assert user.email == "analyst@example.com"


@pytest.mark.asyncio
async def test_supabase_auth_verifier_rejects_invalid_token() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid JWT"})

    verifier = SupabaseAuthVerifier(
        Settings(
            supabase_url="https://project.supabase.co",
            supabase_publishable_key="publishable-key",
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as exc_info:
        await verifier.verify("expired-token")

    assert exc_info.value.status_code == 401
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_authenticated_user_payload_requires_uuid_identity() -> None:
    with pytest.raises(HTTPException) as exc_info:
        authenticated_user_from_payload({"id": "not-a-uuid"})

    assert exc_info.value.status_code == 401


def test_public_research_request_rejects_injected_context() -> None:
    with pytest.raises(ValidationError):
        ResearchRunRequest.model_validate(
            {
                "query": "TCS",
                "mode": "full_analysis",
                "context": {"financials": {"revenue": 999999999}},
            }
        )
