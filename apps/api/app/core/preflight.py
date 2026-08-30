from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_REQUIRED_TABLES = (
    "securities",
    "security_aliases",
    "provider_instruments",
    "research_jobs",
    "sources",
    "evidence_chunks",
    "agent_runs",
    "claims",
    "claim_evidence",
    "research_reports",
    "financial_facts",
    "corporate_events",
    "corporate_event_sources",
    "analysis_snapshots",
    "macro_observations",
    "benchmarks",
    "benchmark_bars",
    "official_data_feeds",
    "official_ingestion_runs",
    "broker_connections",
    "live_market_subscriptions",
    "user_live_quotes",
    "broker_stream_leases",
)

_REQUIRED_RLS_TABLES = (
    "research_jobs",
    "research_reports",
    "agent_runs",
    "claims",
    "claim_evidence",
)


@dataclass(frozen=True)
class DatabasePreflight:
    connected: bool
    vector_extension: bool
    semantic_index: bool
    research_ownership_column: bool
    missing_tables: tuple[str, ...]
    rls_disabled_tables: tuple[str, ...]
    error_type: str | None = None

    @property
    def ready(self) -> bool:
        return bool(
            self.connected
            and self.vector_extension
            and self.semantic_index
            and self.research_ownership_column
            and not self.missing_tables
            and not self.rls_disabled_tables
            and self.error_type is None
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "connected": self.connected,
            "vector_extension": self.vector_extension,
            "semantic_index": self.semantic_index,
            "research_ownership_column": self.research_ownership_column,
            "missing_tables": list(self.missing_tables),
            "rls_disabled_tables": list(self.rls_disabled_tables),
            "error_type": self.error_type,
        }


async def database_preflight(engine: AsyncEngine) -> DatabasePreflight:
    try:
        async with engine.connect() as connection:
            vector_extension = bool(
                await connection.scalar(
                    text("select exists(select 1 from pg_extension where extname = 'vector')")
                )
            )
            semantic_index = bool(
                await connection.scalar(
                    text(
                        "select to_regclass('public.evidence_chunks_embedding_hnsw_idx') is not null"
                    )
                )
            )
            research_ownership_column = bool(
                await connection.scalar(
                    text(
                        """
                        select exists(
                          select 1
                          from information_schema.columns
                          where table_schema = 'public'
                            and table_name = 'research_jobs'
                            and column_name = 'requested_by'
                        )
                        """
                    )
                )
            )
            missing_tables = await _missing_tables(connection)
            rls_disabled_tables = await _rls_disabled_tables(connection)
    except Exception as exc:  # noqa: BLE001 - preflight returns a bounded failure type, not DB details
        return DatabasePreflight(
            connected=False,
            vector_extension=False,
            semantic_index=False,
            research_ownership_column=False,
            missing_tables=(),
            rls_disabled_tables=(),
            error_type=type(exc).__name__,
        )

    return DatabasePreflight(
        connected=True,
        vector_extension=vector_extension,
        semantic_index=semantic_index,
        research_ownership_column=research_ownership_column,
        missing_tables=missing_tables,
        rls_disabled_tables=rls_disabled_tables,
    )


async def _missing_tables(connection: AsyncConnection) -> tuple[str, ...]:
    rows = (
        await connection.execute(
            text(
                """
                with required(name) as (
                  select unnest(cast(:names as text[]))
                )
                select name
                from required
                where to_regclass('public.' || name) is null
                order by name
                """
            ),
            {"names": list(_REQUIRED_TABLES)},
        )
    ).scalars().all()
    return tuple(str(row) for row in rows)


async def _rls_disabled_tables(connection: AsyncConnection) -> tuple[str, ...]:
    rows = (
        await connection.execute(
            text(
                """
                select c.relname
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'public'
                  and c.relname = any(cast(:names as text[]))
                  and c.relkind = 'r'
                  and not c.relrowsecurity
                order by c.relname
                """
            ),
            {"names": list(_REQUIRED_RLS_TABLES)},
        )
    ).scalars().all()
    return tuple(str(row) for row in rows)
