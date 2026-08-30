from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

Row = Mapping[str, Any]


class BrokerRepository:
    """Backend-only persistence for OAuth, broker connections and private live stream state."""

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
            await connection.execute(
                text(
                    "delete from live_market_subscriptions "
                    "where user_id = :user_id and provider = :provider"
                ),
                {"user_id": user_id, "provider": provider},
            )
            await connection.execute(
                text(
                    "delete from user_live_quotes "
                    "where user_id = :user_id and provider = :provider"
                ),
                {"user_id": user_id, "provider": provider},
            )
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

    async def ensure_live_subscription(
        self,
        *,
        user_id: UUID,
        security_id: UUID,
        provider: str,
        ttl_seconds: int,
        mode: str = "ltpc",
    ) -> None:
        active_until = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    insert into live_market_subscriptions (
                        user_id, security_id, provider, mode, active_until, updated_at
                    ) values (
                        :user_id, :security_id, :provider, :mode, :active_until, now()
                    )
                    on conflict (user_id, security_id, provider) do update set
                        mode = excluded.mode,
                        active_until = greatest(
                            live_market_subscriptions.active_until,
                            excluded.active_until
                        ),
                        updated_at = now()
                    """
                ),
                {
                    "user_id": user_id,
                    "security_id": security_id,
                    "provider": provider,
                    "mode": mode,
                    "active_until": active_until,
                },
            )

    async def active_live_instruments(self, user_id: UUID, provider: str) -> list[Row]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    select lms.security_id, lms.mode, lms.active_until,
                           pi.instrument_id, s.isin, s.primary_exchange, s.nse_symbol, s.bse_code
                    from live_market_subscriptions lms
                    join securities s on s.id = lms.security_id
                    left join lateral (
                      select instrument_id
                      from provider_instruments
                      where security_id = lms.security_id and provider = lms.provider
                      order by updated_at desc
                      limit 1
                    ) pi on true
                    where lms.user_id = :user_id
                      and lms.provider = :provider
                      and lms.active_until > now()
                    order by lms.updated_at desc
                    """
                ),
                {"user_id": user_id, "provider": provider},
            )
            return list(result.mappings().all())

    async def stream_candidates(self, provider: str, *, limit: int) -> list[UUID]:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    select distinct lms.user_id
                    from live_market_subscriptions lms
                    join broker_connections bc
                      on bc.user_id = lms.user_id and bc.provider = lms.provider
                    where lms.provider = :provider
                      and lms.active_until > now()
                      and bc.status = 'active'
                      and (bc.token_expires_at is null or bc.token_expires_at > now())
                    order by lms.user_id
                    limit :limit
                    """
                ),
                {"provider": provider, "limit": limit},
            )
            return list(result.scalars().all())

    async def fresh_live_quote(
        self,
        *,
        user_id: UUID,
        security_id: UUID,
        provider: str,
        max_age_seconds: int,
    ) -> Row | None:
        cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    select instrument_id, last_price, close_price, last_trade_at, received_at,
                           bid, ask, volume, market_status, payload
                    from user_live_quotes
                    where user_id = :user_id
                      and security_id = :security_id
                      and provider = :provider
                      and received_at >= :cutoff
                    """
                ),
                {
                    "user_id": user_id,
                    "security_id": security_id,
                    "provider": provider,
                    "cutoff": cutoff,
                },
            )
            return result.mappings().first()

    async def upsert_live_quotes(
        self,
        *,
        user_id: UUID,
        provider: str,
        quotes: list[dict[str, object]],
    ) -> None:
        if not quotes:
            return
        statement = text(
            """
            insert into user_live_quotes (
                user_id, security_id, provider, instrument_id, last_price, close_price,
                last_trade_at, received_at, bid, ask, volume, market_status, payload
            ) values (
                :user_id, :security_id, :provider, :instrument_id, :last_price, :close_price,
                :last_trade_at, :received_at, :bid, :ask, :volume, :market_status,
                cast(:payload as jsonb)
            )
            on conflict (user_id, security_id, provider) do update set
                instrument_id = excluded.instrument_id,
                last_price = excluded.last_price,
                close_price = excluded.close_price,
                last_trade_at = excluded.last_trade_at,
                received_at = excluded.received_at,
                bid = excluded.bid,
                ask = excluded.ask,
                volume = excluded.volume,
                market_status = excluded.market_status,
                payload = excluded.payload
            """
        )
        async with self.engine.begin() as connection:
            for quote in quotes:
                params = dict(quote)
                params["user_id"] = user_id
                params["provider"] = provider
                params["payload"] = json.dumps(quote.get("payload") or {})
                await connection.execute(statement, params)

    async def acquire_stream_lease(
        self,
        *,
        user_id: UUID,
        provider: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        leased_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    insert into broker_stream_leases (
                        user_id, provider, worker_id, leased_until, heartbeat_at
                    ) values (
                        :user_id, :provider, :worker_id, :leased_until, now()
                    )
                    on conflict (user_id, provider) do update set
                        worker_id = excluded.worker_id,
                        leased_until = excluded.leased_until,
                        heartbeat_at = now()
                    where broker_stream_leases.leased_until <= now()
                       or broker_stream_leases.worker_id = excluded.worker_id
                    returning worker_id
                    """
                ),
                {
                    "user_id": user_id,
                    "provider": provider,
                    "worker_id": worker_id,
                    "leased_until": leased_until,
                },
            )
            return result.scalar_one_or_none() == worker_id

    async def heartbeat_stream_lease(
        self,
        *,
        user_id: UUID,
        provider: str,
        worker_id: str,
        lease_seconds: int,
    ) -> bool:
        leased_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    update broker_stream_leases
                    set leased_until = :leased_until, heartbeat_at = now()
                    where user_id = :user_id
                      and provider = :provider
                      and worker_id = :worker_id
                    returning worker_id
                    """
                ),
                {
                    "user_id": user_id,
                    "provider": provider,
                    "worker_id": worker_id,
                    "leased_until": leased_until,
                },
            )
            return result.scalar_one_or_none() == worker_id

    async def release_stream_lease(
        self,
        *,
        user_id: UUID,
        provider: str,
        worker_id: str,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    delete from broker_stream_leases
                    where user_id = :user_id
                      and provider = :provider
                      and worker_id = :worker_id
                    """
                ),
                {"user_id": user_id, "provider": provider, "worker_id": worker_id},
            )
