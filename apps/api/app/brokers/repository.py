from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

Row = Mapping[str, Any]


class BrokerRepository:
    """Backend-only persistence for OAuth state and encrypted broker connections."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create_oauth_state(
        self,
        *,
        user_id: UUID,
        provider: str,
        state_hash: str,
        expires_at: datetime,
        metadata: dict[str, object] | None = None,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into broker_oauth_states (
                        user_id, provider, state_hash, expires_at, metadata
                    ) values (
                        :user_id, :provider, :state_hash, :expires_at, cast(:metadata as jsonb)
                    )
                    """
                ),
                {
                    "user_id": user_id,
                    "provider": provider,
                    "state_hash": state_hash,
                    "expires_at": expires_at,
                    "metadata": json.dumps(metadata or {}),
                },
            )

    async def consume_oauth_state(
        self,
        *,
        provider: str,
        state_hash: str,
        now: datetime | None = None,
    ) -> UUID | None:
        consumed_at = now or datetime.now(UTC)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    update broker_oauth_states
                    set consumed_at = :now
                    where provider = :provider
                      and state_hash = :state_hash
                      and consumed_at is null
                      and expires_at > :now
                    returning user_id
                    """
                ),
                {"provider": provider, "state_hash": state_hash, "now": consumed_at},
            )
            return result.scalar_one_or_none()

    async def upsert_connection(
        self,
        *,
        user_id: UUID,
        provider: str,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None,
        token_expires_at: datetime | None,
        provider_user_id: str | None,
        provider_user_name: str | None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into broker_connections (
                        user_id, provider, encrypted_access_token, encrypted_refresh_token,
                        token_expires_at, provider_user_id, provider_user_name,
                        status, metadata, updated_at
                    ) values (
                        :user_id, :provider, :encrypted_access_token, :encrypted_refresh_token,
                        :token_expires_at, :provider_user_id, :provider_user_name,
                        'active', cast(:metadata as jsonb), now()
                    )
                    on conflict (user_id, provider) do update set
                        encrypted_access_token = excluded.encrypted_access_token,
                        encrypted_refresh_token = excluded.encrypted_refresh_token,
                        token_expires_at = excluded.token_expires_at,
                        provider_user_id = excluded.provider_user_id,
                        provider_user_name = excluded.provider_user_name,
                        status = 'active',
                        metadata = excluded.metadata,
                        updated_at = now()
                    """
                ),
                {
                    "user_id": user_id,
                    "provider": provider,
                    "encrypted_access_token": encrypted_access_token,
                    "encrypted_refresh_token": encrypted_refresh_token,
                    "token_expires_at": token_expires_at,
                    "provider_user_id": provider_user_id,
                    "provider_user_name": provider_user_name,
                    "metadata": json.dumps(metadata or {}),
                },
            )

    async def get_connection(self, user_id: UUID, provider: str) -> Row | None:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    select provider, encrypted_access_token, encrypted_refresh_token,
                           token_expires_at, provider_user_id, provider_user_name,
                           status, metadata, created_at, updated_at
                    from broker_connections
                    where user_id = :user_id and provider = :provider
                    """
                ),
                {"user_id": user_id, "provider": provider},
            )
            return result.mappings().first()

    async def connection_status(self, user_id: UUID, provider: str) -> dict[str, object]:
        row = await self.get_connection(user_id, provider)
        if row is None:
            return {"provider": provider, "connected": False, "status": "disconnected"}
        expires_at = row.get("token_expires_at")
        expired = bool(expires_at and expires_at <= datetime.now(UTC))
        status = "expired" if expired else str(row.get("status") or "unknown")
        return {
            "provider": provider,
            "connected": status == "active",
            "status": status,
            "token_expires_at": expires_at.isoformat() if expires_at else None,
            "provider_user_id": row.get("provider_user_id"),
            "provider_user_name": row.get("provider_user_name"),
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    async def disconnect(self, user_id: UUID, provider: str) -> bool:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    "delete from broker_connections where user_id = :user_id and provider = :provider"
                ),
                {"user_id": user_id, "provider": provider},
            )
            return bool(result.rowcount)

    async def provider_instrument(self, security_id: UUID, provider: str) -> Row | None:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    select instrument_id, exchange_segment, trading_symbol, metadata
                    from provider_instruments
                    where security_id = :security_id and provider = :provider
                    order by updated_at desc
                    limit 1
                    """
                ),
                {"security_id": security_id, "provider": provider},
            )
            return result.mappings().first()
