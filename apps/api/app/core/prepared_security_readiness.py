from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import AgentName
from app.core.config import Settings
from app.core.data_readiness import load_data_coverage
from app.core.security_readiness import (
    SecurityReadiness,
    evaluate_security_readiness,
    load_security_agent_coverage,
)

SECURITY_READINESS_RULE_VERSION = "security-agent-readiness-v1"


@dataclass(frozen=True)
class SecurityReadinessFreshness:
    latest_market_session: date | None
    latest_financial_period: date | None
    latest_filing_at: datetime | None
    latest_earnings_at: datetime | None


def build_prepared_agent_rows(
    readiness: SecurityReadiness,
    freshness: SecurityReadinessFreshness,
    *,
    evaluated_at: datetime,
    rule_version: str = SECURITY_READINESS_RULE_VERSION,
) -> tuple[dict[str, object], ...]:
    """Build one immutable persistence row for every evaluated agent."""

    evaluated = _utc(evaluated_at)
    coverage = readiness.report.coverage.as_dict()
    blocking_agents = list(readiness.blocking_agents)
    return tuple(
        {
            "security_id": readiness.security_id,
            "agent_name": item.agent.value,
            "ready": item.ready,
            "errors": list(item.errors),
            "warnings": list(item.warnings),
            "coverage": coverage,
            "blocking_agents": blocking_agents,
            "latest_market_session": freshness.latest_market_session,
            "latest_financial_period": freshness.latest_financial_period,
            "latest_filing_at": freshness.latest_filing_at,
            "latest_earnings_at": freshness.latest_earnings_at,
            "rule_version": rule_version,
            "evaluated_at": evaluated,
        }
        for item in readiness.report.agents
    )


async def refresh_prepared_security_readiness(
    engine: AsyncEngine,
    security_id: UUID,
    settings: Settings,
    *,
    evaluated_at: datetime | None = None,
) -> SecurityReadiness:
    """Re-evaluate one security from source data and atomically persist all agent states."""

    evaluated = _utc(evaluated_at or datetime.now(UTC))
    corpus_coverage = await load_data_coverage(engine)
    security_coverage, symbol = await load_security_agent_coverage(engine, security_id)
    readiness = evaluate_security_readiness(
        security_id,
        symbol,
        security_coverage,
        corpus_coverage,
        settings,
        as_of=evaluated,
    )

    await persist_prepared_security_readiness(
        engine,
        readiness,
        evaluated_at=evaluated,
    )
    return readiness


async def persist_prepared_security_readiness(
    engine: AsyncEngine,
    readiness: SecurityReadiness,
    *,
    evaluated_at: datetime | None = None,
) -> None:
    """Persist a readiness result already evaluated from live source tables."""

    evaluated = _utc(evaluated_at or datetime.now(UTC))
    freshness = await _load_freshness(engine, readiness.security_id)
    rows = build_prepared_agent_rows(readiness, freshness, evaluated_at=evaluated)
    expected_agents = {agent.value for agent in AgentName}
    actual_agents = {str(row["agent_name"]) for row in rows}
    if actual_agents != expected_agents:
        raise RuntimeError("prepared readiness did not evaluate every configured agent")

    statement = text(
        """
        insert into security_agent_readiness_status (
          security_id, agent_name, ready, errors, warnings, coverage, blocking_agents,
          latest_market_session, latest_financial_period, latest_filing_at,
          latest_earnings_at, rule_version, evaluated_at, updated_at
        ) values (
          :security_id, :agent_name, :ready, cast(:errors as jsonb),
          cast(:warnings as jsonb), cast(:coverage as jsonb),
          cast(:blocking_agents as jsonb), :latest_market_session,
          :latest_financial_period, :latest_filing_at, :latest_earnings_at,
          :rule_version, :evaluated_at, now()
        )
        on conflict (security_id, agent_name) do update set
          ready = excluded.ready,
          errors = excluded.errors,
          warnings = excluded.warnings,
          coverage = excluded.coverage,
          blocking_agents = excluded.blocking_agents,
          latest_market_session = excluded.latest_market_session,
          latest_financial_period = excluded.latest_financial_period,
          latest_filing_at = excluded.latest_filing_at,
          latest_earnings_at = excluded.latest_earnings_at,
          rule_version = excluded.rule_version,
          evaluated_at = excluded.evaluated_at,
          updated_at = now()
        """
    )
    serialized_rows = [
        {
            **row,
            "errors": json.dumps(row["errors"]),
            "warnings": json.dumps(row["warnings"]),
            "coverage": json.dumps(row["coverage"]),
            "blocking_agents": json.dumps(row["blocking_agents"]),
        }
        for row in rows
    ]
    async with engine.begin() as connection:
        await connection.execute(statement, serialized_rows)
        await connection.execute(
            text(
                """
                delete from security_agent_readiness_status
                where security_id = :security_id
                  and agent_name <> all(cast(:agent_names as text[]))
                """
            ),
            {
                "security_id": security_id,
                "agent_names": sorted(expected_agents),
            },
        )


async def _load_freshness(
    engine: AsyncEngine,
    security_id: UUID,
) -> SecurityReadinessFreshness:
    statement = text(
        """
        select
          (select max(ts)::date from market_bars where security_id = :security_id)
            as latest_market_session,
          (select max(period_end) from financial_facts where security_id = :security_id)
            as latest_financial_period,
          (
            select max(coalesce(src.published_at, src.retrieved_at))
            from sources src
            where src.security_id = :security_id
              and src.source_type in ('exchange_filing', 'company_filing', 'regulator')
              and exists (
                select 1 from evidence_chunks ec
                where ec.source_id = src.id and length(btrim(ec.content)) > 0
              )
          ) as latest_filing_at,
          (
            select max(coalesce(src.published_at, ce.event_at, src.retrieved_at))
            from corporate_events ce
            join corporate_event_sources ces on ces.event_id = ce.id
            join sources src on src.id = ces.source_id
            where ce.security_id = :security_id
              and ces.parse_status = 'parsed'
              and (
                ce.event_type in (
                  'financial_results', 'earnings_call', 'earnings_transcript',
                  'investor_presentation'
                )
                or ces.document_role in ('transcript', 'presentation', 'xbrl')
              )
              and exists (
                select 1 from evidence_chunks ec
                where ec.source_id = src.id and length(btrim(ec.content)) > 0
              )
          ) as latest_earnings_at
        """
    )
    async with engine.connect() as connection:
        row = (
            await connection.execute(statement, {"security_id": security_id})
        ).mappings().one()
    return SecurityReadinessFreshness(
        latest_market_session=row.get("latest_market_session"),
        latest_financial_period=row.get("latest_financial_period"),
        latest_filing_at=row.get("latest_filing_at"),
        latest_earnings_at=row.get("latest_earnings_at"),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
