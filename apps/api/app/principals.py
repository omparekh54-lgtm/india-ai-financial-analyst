"""Principals: the single ownership identity for authenticated and anonymous visitors.

PROJECT_INTENT.md requires that "every visitor must receive an isolated, signed anonymous
session" and prohibits "using one shared pseudo-user for all visitors" and "using NULL
ownership as a substitute for public-session isolation".

Recorded deviation from the intent's wording: the intent asks for a *signed* cookie. This
module issues a 256-bit random opaque token instead and stores only its SHA-256 digest.
That is strictly stronger for this use: a signed cookie is self-validating and therefore
cannot be revoked or expired server-side, whereas a stored hash can be deleted on request
(needed for the retention policy the intent also requires) and leaks nothing if the table
is read. No signing secret has to be provisioned or rotated.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

SESSION_COOKIE_NAME = "iafa_session"
SESSION_TOKEN_BYTES = 32
ANONYMOUS_SESSION_TTL = timedelta(days=30)


@dataclass(frozen=True)
class Principal:
    """Resolved owner of a request."""

    id: UUID
    kind: str
    auth_user_id: UUID | None = None
    email: str | None = None

    @property
    def is_anonymous(self) -> bool:
        return self.kind == "anonymous"


def new_session_token() -> str:
    """Non-guessable opaque session token. Never stored; only its digest is persisted."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PrincipalRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def resolve_anonymous(self, token: str | None) -> tuple[Principal, str | None]:
        """Resolve an anonymous session token, issuing a new session when needed.

        Returns the principal and, when a new session was created, the plaintext token the
        caller must set as a cookie. An expired or unknown token yields a fresh session
        rather than an error, so a returning visitor with a stale cookie is never blocked.
        """
        now = datetime.now(UTC)
        if token:
            digest = hash_session_token(token)
            async with self.engine.begin() as connection:
                row = (
                    await connection.execute(
                        text(
                            """
                            update anonymous_sessions
                               set last_seen_at = :now
                             where token_sha256 = :digest
                               and expires_at > :now
                            returning principal_id
                            """
                        ),
                        {"digest": digest, "now": now},
                    )
                ).mappings().first()
                if row is not None:
                    principal_id = UUID(str(row["principal_id"]))
                    await connection.execute(
                        text(
                            """
                            update principals
                               set last_seen_at = :now,
                                   expires_at = :expires_at
                             where id = :id
                            """
                        ),
                        {"id": principal_id, "now": now, "expires_at": now + ANONYMOUS_SESSION_TTL},
                    )
                    return Principal(id=principal_id, kind="anonymous"), None

        return await self._create_anonymous_session(now)

    async def _create_anonymous_session(self, now: datetime) -> tuple[Principal, str]:
        token = new_session_token()
        expires_at = now + ANONYMOUS_SESSION_TTL
        async with self.engine.begin() as connection:
            principal_id = await connection.scalar(
                text(
                    """
                    insert into principals (kind, expires_at)
                    values ('anonymous', :expires_at)
                    returning id
                    """
                ),
                {"expires_at": expires_at},
            )
            await connection.execute(
                text(
                    """
                    insert into anonymous_sessions (token_sha256, principal_id, expires_at)
                    values (:digest, :principal_id, :expires_at)
                    """
                ),
                {
                    "digest": hash_session_token(token),
                    "principal_id": principal_id,
                    "expires_at": expires_at,
                },
            )
        return Principal(id=UUID(str(principal_id)), kind="anonymous"), token

    async def resolve_user(self, auth_user_id: UUID, email: str | None) -> Principal:
        """Return the principal for a signed-in Supabase user, creating it on first sight."""
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            principal_id = await connection.scalar(
                text(
                    """
                    insert into principals (kind, auth_user_id)
                    values ('user', :auth_user_id)
                    on conflict (auth_user_id) do update
                       set last_seen_at = :now
                    returning id
                    """
                ),
                {"auth_user_id": auth_user_id, "now": now},
            )
        return Principal(
            id=UUID(str(principal_id)),
            kind="user",
            auth_user_id=auth_user_id,
            email=email,
        )

    async def purge_expired(self) -> int:
        """Delete expired anonymous sessions and principals. Owned rows cascade.

        Implements the retention policy the intent requires. Intended to run on a schedule.
        """
        now = datetime.now(UTC)
        async with self.engine.begin() as connection:
            await connection.execute(
                text("delete from anonymous_sessions where expires_at <= :now"), {"now": now}
            )
            result = await connection.execute(
                text(
                    """
                    delete from principals
                     where kind = 'anonymous'
                       and expires_at is not null
                       and expires_at <= :now
                    """
                ),
                {"now": now},
            )
        return int(result.rowcount or 0)
