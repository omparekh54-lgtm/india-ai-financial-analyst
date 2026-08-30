from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: str | None = None


class SupabaseAuthVerifier:
    """Validates a browser access token against Supabase Auth without handling JWT secrets."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def verify(self, access_token: str) -> AuthenticatedUser:
        token = access_token.strip()
        if not token:
            raise _unauthorized("Missing access token")
        if not self.settings.supabase_url or not self.settings.supabase_publishable_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase authentication is not configured",
            )

        url = f"{self.settings.supabase_url.rstrip('/')}/auth/v1/user"
        headers = {
            "Authorization": f"Bearer {token}",
            "apikey": self.settings.supabase_publishable_key,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                transport=self.transport,
            ) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication provider is temporarily unavailable",
            ) from exc

        if response.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
            raise _unauthorized("Invalid or expired access token")
        if response.status_code >= 500:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication provider is temporarily unavailable",
            )
        if response.status_code != status.HTTP_200_OK:
            raise _unauthorized("Access token could not be verified")

        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication provider returned an invalid response",
            ) from exc
        return authenticated_user_from_payload(payload)


def authenticated_user_from_payload(payload: object) -> AuthenticatedUser:
    if not isinstance(payload, dict) or not payload.get("id"):
        raise _unauthorized("Authenticated user identity is missing")
    try:
        user_id = UUID(str(payload["id"]))
    except (TypeError, ValueError) as exc:
        raise _unauthorized("Authenticated user identity is invalid") from exc
    email_value = payload.get("email")
    email = str(email_value).strip() if email_value else None
    return AuthenticatedUser(id=user_id, email=email or None)


async def require_authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Bearer authentication is required")
    return await SupabaseAuthVerifier(settings).verify(credentials.credentials)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
