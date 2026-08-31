from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class WatchlistRepository:
    """Server-side watchlist access with explicit ownership checks on every user mutation."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def list_for_user(self, user_id: UUID) -> list[dict[str, object]]:
        statement = text(
            """
            select
              w.id as watchlist_id,
              w.name,
              w.created_at,
              w.updated_at,
              wi.security_id,
              wi.notes,
              wi.event_research_enabled,
              wi.added_at,
              s.legal_name,
              s.nse_symbol,
              s.bse_code,
              s.isin,
              s.sector,
              s.industry
            from watchlists w
            left join watchlist_items wi on wi.watchlist_id = w.id
            left join securities s on s.id = wi.security_id
            where w.user_id = :user_id
            order by w.updated_at desc, w.name, wi.added_at desc nulls last
            """
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement, {"user_id": user_id})).mappings().all()

        grouped: dict[UUID, dict[str, object]] = {}
        for row in rows:
            watchlist_id = UUID(str(row["watchlist_id"]))
            watchlist = grouped.setdefault(
                watchlist_id,
                {
                    "id": str(watchlist_id),
                    "name": row["name"],
                    "created_at": _iso(row["created_at"]),
                    "updated_at": _iso(row["updated_at"]),
                    "items": [],
                },
            )
            if row["security_id"] is None:
                continue
            items = watchlist["items"]
            assert isinstance(items, list)
            items.append(
                {
                    "security_id": str(row["security_id"]),
                    "legal_name": row["legal_name"],
                    "nse_symbol": row["nse_symbol"],
                    "bse_code": row["bse_code"],
                    "isin": row["isin"],
                    "sector": row["sector"],
                    "industry": row["industry"],
                    "notes": row["notes"],
                    "event_research_enabled": bool(row["event_research_enabled"]),
                    "added_at": _iso(row["added_at"]),
                }
            )
        return list(grouped.values())

    async def create(self, user_id: UUID, name: str) -> dict[str, object]:
        statement = text(
            """
            insert into watchlists (user_id, name)
            values (:user_id, :name)
            returning id, name, created_at, updated_at
            """
        )
        async with self.engine.begin() as connection:
            row = (
                await connection.execute(statement, {"user_id": user_id, "name": name.strip()})
            ).mappings().one()
        return {
            "id": str(row["id"]),
            "name": row["name"],
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "items": [],
        }

    async def add_item(
        self,
        user_id: UUID,
        watchlist_id: UUID,
        security_id: UUID,
        *,
        notes: str | None = None,
        event_research_enabled: bool = True,
    ) -> dict[str, object] | None:
        statement = text(
            """
            insert into watchlist_items (
              watchlist_id, security_id, notes, event_research_enabled, updated_at
            )
            select :watchlist_id, :security_id, :notes, :event_research_enabled, now()
            where exists (
              select 1 from watchlists
              where id = :watchlist_id and user_id = :user_id
            )
            on conflict (watchlist_id, security_id) do update
            set notes = excluded.notes,
                event_research_enabled = excluded.event_research_enabled,
                updated_at = now()
            returning watchlist_id, security_id, notes, event_research_enabled, added_at
            """
        )
        params = {
            "watchlist_id": watchlist_id,
            "security_id": security_id,
            "user_id": user_id,
            "notes": notes.strip() if notes else None,
            "event_research_enabled": event_research_enabled,
        }
        async with self.engine.begin() as connection:
            row = (await connection.execute(statement, params)).mappings().first()
            if row is not None:
                await connection.execute(
                    text("update watchlists set updated_at = now() where id = :watchlist_id"),
                    {"watchlist_id": watchlist_id},
                )
        if row is None:
            return None
        return {
            "watchlist_id": str(row["watchlist_id"]),
            "security_id": str(row["security_id"]),
            "notes": row["notes"],
            "event_research_enabled": bool(row["event_research_enabled"]),
            "added_at": _iso(row["added_at"]),
        }

    async def remove_item(
        self,
        user_id: UUID,
        watchlist_id: UUID,
        security_id: UUID,
    ) -> bool:
        statement = text(
            """
            delete from watchlist_items wi
            using watchlists w
            where wi.watchlist_id = w.id
              and wi.watchlist_id = :watchlist_id
              and wi.security_id = :security_id
              and w.user_id = :user_id
            returning wi.security_id
            """
        )
        async with self.engine.begin() as connection:
            removed = await connection.scalar(
                statement,
                {
                    "watchlist_id": watchlist_id,
                    "security_id": security_id,
                    "user_id": user_id,
                },
            )
            if removed is not None:
                await connection.execute(
                    text("update watchlists set updated_at = now() where id = :watchlist_id"),
                    {"watchlist_id": watchlist_id},
                )
        return removed is not None

    async def event_subscribers(self, security_id: UUID) -> list[UUID]:
        statement = text(
            """
            select distinct w.user_id
            from watchlist_items wi
            join watchlists w on w.id = wi.watchlist_id
            where wi.security_id = :security_id
              and wi.event_research_enabled = true
            order by w.user_id
            """
        )
        async with self.engine.connect() as connection:
            values = (await connection.scalars(statement, {"security_id": security_id})).all()
        return [UUID(str(value)) for value in values]

    async def user_owns(self, user_id: UUID, watchlist_id: UUID) -> bool:
        statement = text(
            "select exists(select 1 from watchlists where id = :id and user_id = :user_id)"
        )
        async with self.engine.connect() as connection:
            return bool(
                await connection.scalar(statement, {"id": watchlist_id, "user_id": user_id})
            )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)
