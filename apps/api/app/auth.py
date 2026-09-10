from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.db import create_database_engine
from app.principals import (
    ANONYMOUS_SESSION_TTL,
    SESSION_COOKIE_NAME,
    Principal,
    PrincipalRepository,
)

_bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True)
class AuthenticatedUser:
    """Owner of a request.

    `id` is a principal id, not a Supabase auth user id. Ownership columns reference
    `principals`, so anonymous visitors and signed-in users are isolated by one mechanism.
    There is deliberately no shared public identity: PROJECT_INTENT.md prohibits it.
    """

    id: UUID
    email: str | None = None
    kind: str = "user"
    auth_user_id: UUID | None = None

    @property
    def is_anonymous(self) -> bool:
        return self.kind == "anonymous"

    @classmethod
    def from_principal(cls, principal: Principal) -> "AuthenticatedUser":
        return cls(
            id=principal.id,
            email=principal.email,
            kind=principal.kind,
            auth_user_id=principal.auth_user_id,
        )


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
    request: Request,
    response: Response,
    credentials: BearerCredentials,
    settings: SettingsDependency,
) -> AuthenticatedUser:
    """Resolve the request owner to a principal.

    A bearer token resolves to that user's principal. Anything else is an anonymous
    visitor, who gets their own durable session rather than a shared identity.
    """
    if not settings.database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DATABASE_URL is not configured",
        )
    repository = PrincipalRepository(create_database_engine(settings.database_url))

    if credentials is not None and credentials.scheme.lower() == "bearer":
        user = await SupabaseAuthVerifier(settings).verify(credentials.credentials)
        principal = await repository.resolve_user(user.id, user.email)
        return AuthenticatedUser.from_principal(principal)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    principal, issued_token = await repository.resolve_anonymous(token)
    if issued_token is not None:
        response.set_cookie(
            SESSION_COOKIE_NAME,
            issued_token,
            max_age=int(ANONYMOUS_SESSION_TTL.total_seconds()),
            httponly=True,
            secure=settings.app_env.strip().lower() == "production",
            samesite="lax",
            path="/",
        )
    return AuthenticatedUser.from_principal(principal)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
