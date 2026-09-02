from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from app.brokers.crypto import BrokerTokenCipher
from app.brokers.repository import BrokerRepository
from app.core.config import Settings

_UPSTOX_AUTHORIZE_URL = "https://api.upstox.com/v2/login/authorization/dialog"
_UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
_IST = ZoneInfo("Asia/Kolkata")


class UpstoxOAuthError(RuntimeError):
    pass


class UpstoxOAuthService:
    provider = "upstox"

    def __init__(
        self,
        repository: BrokerRepository,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.transport = transport

    async def begin(self, user_id: UUID) -> str:
        client_id, _, redirect_uri, _ = self._configuration()
        state = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        await self.repository.create_oauth_state(
            user_id=user_id,
            provider=self.provider,
            state_hash=_state_hash(state),
            expires_at=now + timedelta(seconds=self.settings.broker_oauth_state_ttl_seconds),
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
            }
        )
        return f"{_UPSTOX_AUTHORIZE_URL}?{query}"

    async def complete(self, *, code: str, state: str) -> dict[str, object]:
        auth_code = code.strip()
        raw_state = state.strip()
        if not auth_code or not raw_state:
            raise UpstoxOAuthError("Upstox callback is missing code or state")

        user_id = await self.repository.consume_oauth_state(
            provider=self.provider,
            state_hash=_state_hash(raw_state),
        )
        if user_id is None:
            raise UpstoxOAuthError("Upstox OAuth state is invalid, expired, or already used")

        client_id, client_secret, redirect_uri, cipher = self._configuration()
        payload = await self._exchange_code(
            code=auth_code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
        access_token = _required_string(payload, "access_token")
        refresh_token = _optional_string(payload.get("refresh_token"))
        expires_at = _upstox_token_expiry(datetime.now(UTC))

        await self.repository.upsert_connection(
            user_id=user_id,
            provider=self.provider,
            encrypted_access_token=cipher.encrypt(access_token),
            encrypted_refresh_token=cipher.encrypt(refresh_token) if refresh_token else None,
            token_expires_at=expires_at,
            provider_user_id=_optional_string(payload.get("user_id")),
            provider_user_name=_optional_string(payload.get("user_name")),
            metadata=_safe_profile_metadata(payload),
        )
        return await self.repository.connection_status(user_id, self.provider)

    async def access_token_for_user(self, user_id: UUID) -> str | None:
        row = await self.repository.get_connection(user_id, self.provider)
        if row is None or row.get("status") != "active":
            return None
        expires_at = row.get("token_expires_at")
        if expires_at is not None and expires_at <= datetime.now(UTC):
            return None
        _, _, _, cipher = self._configuration()
        encrypted = row.get("encrypted_access_token")
        if not encrypted:
            return None
        return cipher.decrypt(str(encrypted))

    async def status(self, user_id: UUID) -> dict[str, object]:
        return await self.repository.connection_status(user_id, self.provider)

    async def disconnect(self, user_id: UUID) -> bool:
        return await self.repository.disconnect(user_id, self.provider)

    async def _exchange_code(
        self,
        *,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        form = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                transport=self.transport,
            ) as client:
                response = await client.post(
                    _UPSTOX_TOKEN_URL,
                    data=form,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise UpstoxOAuthError("Upstox token exchange is temporarily unavailable") from exc

        if response.status_code >= 400:
            raise UpstoxOAuthError(
                f"Upstox token exchange failed with HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstoxOAuthError("Upstox token exchange returned invalid JSON") from exc
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise UpstoxOAuthError("Upstox token exchange did not return an access token")
        return payload

    def _configuration(self) -> tuple[str, str, str, BrokerTokenCipher]:
        client_id = (self.settings.upstox_client_id or "").strip()
        client_secret = (self.settings.upstox_client_secret or "").strip()
        redirect_uri = (self.settings.upstox_redirect_uri or "").strip()
        encryption_key = (self.settings.broker_token_encryption_key or "").strip()
        if not client_id or not client_secret or not redirect_uri:
            raise UpstoxOAuthError("Upstox OAuth application credentials are not configured")
        if not encryption_key:
            raise UpstoxOAuthError("Broker token encryption is not configured")
        return client_id, client_secret, redirect_uri, BrokerTokenCipher(encryption_key)


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _upstox_token_expiry(now: datetime) -> datetime:
    local_now = now.astimezone(_IST)
    cutoff = datetime.combine(local_now.date(), time(hour=3, minute=30), tzinfo=_IST)
    if local_now >= cutoff:
        cutoff += timedelta(days=1)
    return cutoff.astimezone(UTC)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = _optional_string(payload.get(key))
    if not value:
        raise UpstoxOAuthError(f"Upstox response is missing {key}")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_profile_metadata(payload: dict[str, Any]) -> dict[str, object]:
    allowed = (
        "broker",
        "email",
        "exchanges",
        "products",
        "order_types",
        "user_type",
        "poa",
        "is_active",
    )
    return {key: payload[key] for key in allowed if key in payload}
