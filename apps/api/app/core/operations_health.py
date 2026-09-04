from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.agent_data_readiness import evaluate_agent_readiness, load_agent_data_coverage
from app.core.config import Settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage


@dataclass(frozen=True)
class OperationsHealthReport:
    ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    diagnostics: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "diagnostics": self.diagnostics,
        }


async def load_operations_health(
    engine: AsyncEngine,
    settings: Settings,
    *,
    min_nse_eq_securities: int = 1000,
    as_of: datetime | None = None,
) -> OperationsHealthReport:
    now = as_of or datetime.now(UTC)
    coverage = await load_data_coverage(engine)
    agent_coverage = await load_agent_data_coverage(engine)
    corpus = evaluate_data_coverage(
        coverage,
        min_nse_eq_securities=min_nse_eq_securities,
        as_of=now,
    )
    agents = evaluate_agent_readiness(
        agent_coverage,
        coverage,
        settings,
        min_nse_eq_securities=min_nse_eq_securities,
        as_of=now,
    )

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    select
                      (select count(*) from research_jobs
                         where status='queued' and created_at < :now - interval '15 minutes')
                        as stale_queued_jobs,
                      (select count(*) from research_jobs
                         where status='running'
                           and coalesce(started_at, created_at) < :now - interval '45 minutes')
                        as stale_running_jobs,
                      (select count(*) from ingestion_runs
                         where status='failed' and created_at >= :now - interval '24 hours')
                        as failed_ingestion_runs_24h,
                      (select count(*) from ingestion_runs
                         where status='running'
                           and coalesce(started_at, created_at) < :now - interval '2 hours')
                        as stale_ingestion_runs,
                      (select count(*) from official_ingestion_runs
                         where status='failed' and started_at >= :now - interval '24 hours')
                        as failed_official_runs_24h,
                      (select count(*) from official_data_feeds
                         where enabled and (
                           last_success_at is null
                           or last_success_at < :now - (greatest(poll_interval_seconds * 4, 3600) * interval '1 second')
                         )) as stale_enabled_official_feeds,
                      (select count(*) from evidence_chunks where embedding is null)
                        as unembedded_evidence_chunks,
                      (select count(*)
                         from analysis_snapshots snap
                         where exists (
                           select 1
                           from market_bars mb
                           where mb.security_id=snap.security_id
                             and mb.source_id is not null
                             and mb.interval in ('1d','day','daily')
                             and mb.ts::date > snap.snapshot_at::date
                           group by mb.security_id
                           having count(distinct mb.ts::date) >= 21
                         )
                         and not exists (
                           select 1 from research_evaluations e
                           where e.snapshot_id=snap.id and e.horizon_sessions=20
                         )) as due_20_session_calibrations,
                      (select count(*) from monitoring_alerts where read_at is null)
                        as total_unread_monitoring_alerts,
                      (select count(*) from live_market_subscriptions
                         where active_until > :now) as active_live_subscriptions,
                      (select count(*) from broker_stream_leases
                         where leased_until > :now) as active_live_stream_leases,
                      (select count(*) from user_live_quotes
                         where received_at >= :now - make_interval(secs => :quote_fresh_seconds))
                        as fresh_live_quotes,
                      (select count(*)
                         from live_market_subscriptions sub
                         where sub.active_until > :now
                           and not exists (
                             select 1 from user_live_quotes quote
                             where quote.user_id=sub.user_id
                               and quote.security_id=sub.security_id
                               and quote.provider=sub.provider
                               and quote.received_at >=
                                 :now - make_interval(secs => :quote_fresh_seconds)
                           )) as stale_active_live_subscriptions
                    """
                ),
                {
                    "now": now,
                    "quote_fresh_seconds": settings.live_market_quote_fresh_seconds,
                },
            )
        ).mappings().one()

    diagnostics = {key: _value(value) for key, value in row.items()}
    diagnostics["corpus"] = corpus.as_dict()
    diagnostics["agent_readiness"] = agents.as_dict()

    errors = list(corpus.errors)
    if not agents.ready:
        errors.append(
            "Required research agents are not data-ready: " + ", ".join(agents.blocking_agents)
        )
    if int(row["stale_running_jobs"] or 0) > 0:
        errors.append(f"Stale running research jobs: {row['stale_running_jobs']}")
    if int(row["stale_ingestion_runs"] or 0) > 0:
        errors.append(f"Stale running ingestion jobs: {row['stale_ingestion_runs']}")
    if int(row["stale_enabled_official_feeds"] or 0) > 0:
        errors.append(f"Stale enabled official feeds: {row['stale_enabled_official_feeds']}")

    live_errors, live_warnings = evaluate_live_market_operations(
        enabled=settings.enable_live_market,
        active_subscriptions=int(row["active_live_subscriptions"] or 0),
        active_leases=int(row["active_live_stream_leases"] or 0),
        fresh_quotes=int(row["fresh_live_quotes"] or 0),
        stale_subscriptions=int(row["stale_active_live_subscriptions"] or 0),
    )
    errors.extend(live_errors)

    warnings = list(corpus.warnings)
    if int(row["stale_queued_jobs"] or 0) > 0:
        warnings.append(f"Research jobs queued over 15 minutes: {row['stale_queued_jobs']}")
    if int(row["failed_ingestion_runs_24h"] or 0) > 0:
        warnings.append(f"Failed ingestion runs in 24h: {row['failed_ingestion_runs_24h']}")
    if int(row["failed_official_runs_24h"] or 0) > 0:
        warnings.append(f"Failed official-feed runs in 24h: {row['failed_official_runs_24h']}")
    if settings.enable_semantic_retrieval and int(row["unembedded_evidence_chunks"] or 0) > 0:
        warnings.append(
            f"Evidence chunks waiting for embeddings: {row['unembedded_evidence_chunks']}"
        )
    if int(row["due_20_session_calibrations"] or 0) > 0:
        warnings.append(
            f"Mature snapshots waiting for 20-session calibration: {row['due_20_session_calibrations']}"
        )
    warnings.extend(live_warnings)

    return OperationsHealthReport(
        ready=not errors,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        diagnostics=diagnostics,
    )


def _value(value: Any) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def evaluate_live_market_operations(
    *,
    enabled: bool,
    active_subscriptions: int,
    active_leases: int,
    fresh_quotes: int,
    stale_subscriptions: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not enabled:
        return (), ()

    errors: list[str] = []
    warnings: list[str] = []
    if active_subscriptions == 0:
        warnings.append("Live market is enabled but has no active user subscriptions.")
    else:
        if active_leases == 0:
            errors.append("Live market has active subscriptions but no active stream lease.")
        if fresh_quotes == 0:
            errors.append("Live market has active subscriptions but no fresh quotes.")
        if stale_subscriptions > 0:
            errors.append(
                f"Active live-market subscriptions without fresh quotes: {stale_subscriptions}"
            )
    return tuple(errors), tuple(warnings)
