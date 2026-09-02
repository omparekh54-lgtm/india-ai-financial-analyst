from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

from app.brokers.crypto import BrokerTokenCipher
from app.brokers.upstox_oauth import UpstoxOAuthError, UpstoxOAuthService
from app.core.config import Settings


class FakeBrokerRepository:
    def __init__(self) -> None:
        self.user_id: UUID | None = None
        self.state_hash: str | None = None
        self.state_consumed = False
        self.connection: dict[str, object] | None = None

    async def create_oauth_state(self, **kwargs: object) -> None:
        self.user_id = kwargs["user_id"]  # type: ignore[assignment]
        self.state_hash = str(kwargs["state_hash"])
        self.state_consumed = False

    async def consume_oauth_state(self, **kwargs: object) -> UUID | None:
        if self.state_consumed or str(kwargs["state_hash"]) != self.state_hash:
            return None
        self.state_consumed = True
        return self.user_id

    async def upsert_connection(self, **kwargs: object) -> None:
        self.connection = dict(kwargs)
        self.connection["status"] = "active"
        self.connection["updated_at"] = datetime.now(UTC)

    async def get_connection(self, user_id: UUID, provider: str) -> dict[str, object] | None:
        if self.connection is None:
            return None
        if self.connection.get("user_id") != user_id or self.connection.get("provider") != provider:
            return None
        return self.connection

    async def connection_status(self, user_id: UUID, provider: str) -> dict[str, object]:
        row = await self.get_connection(user_id, provider)
        if row is None:
            return {"provider": provider, "connected": False, "status": "disconnected"}
        expires_at = row.get("token_expires_at")
        return {
            "provider": provider,
            "connected": True,
            "status": "active",
            "token_expires_at": expires_at.isoformat() if isinstance(expires_at, datetime) else None,
        }

    async def disconnect(self, user_id: UUID, provider: str) -> bool:
        if await self.get_connection(user_id, provider) is None:
            return False
        self.connection = None
        return True


def _settings(key: str) -> Settings:
    return Settings(
        upstox_client_id="client-id",
        upstox_client_secret="client-secret",
        upstox_redirect_uri="https://api.example.com/v1/brokers/upstox/callback",
        broker_token_encryption_key=key,
    )


def test_broker_token_cipher_round_trip() -> None:
    key = Fernet.generate_key().decode()
    cipher = BrokerTokenCipher(key)
    encrypted = cipher.encrypt("sensitive-token")

    assert encrypted != "sensitive-token"
    assert cipher.decrypt(encrypted) == "sensitive-token"


@pytest.mark.asyncio
async def test_upstox_oauth_is_single_use_and_stores_only_ciphertext() -> None:
    key = Fernet.generate_key().decode()
    repository = FakeBrokerRepository()
    user_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/login/authorization/token"
        body = request.content.decode()
        assert "client_secret=client-secret" in body
        assert "grant_type=authorization_code" in body
        return httpx.Response(
            200,
            json={
                "access_token": "plain-access-token",
                "user_id": "UP123456",
                "user_name": "Research User",
                "broker": "UPSTOX",
                "exchanges": ["NSE", "BSE"],
            },
        )

    service = UpstoxOAuthService(
        repository,  # type: ignore[arg-type]
        _settings(key),
        transport=httpx.MockTransport(handler),
    )
    authorize_url = await service.begin(user_id)
    query = parse_qs(urlparse(authorize_url).query)
    state = query["state"][0]

    status = await service.complete(code="single-use-code", state=state)

    assert status["connected"] is True
    assert repository.connection is not None
    encrypted = str(repository.connection["encrypted_access_token"])
    assert encrypted != "plain-access-token"
    assert "plain-access-token" not in str(repository.connection)
    assert BrokerTokenCipher(key).decrypt(encrypted) == "plain-access-token"
    assert await service.access_token_for_user(user_id) == "plain-access-token"

    with pytest.raises(UpstoxOAuthError, match="already used"):
        await service.complete(code="another-code", state=state)
