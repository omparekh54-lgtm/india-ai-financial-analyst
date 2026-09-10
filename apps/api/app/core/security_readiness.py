"""Per-security agent readiness.

`agent_data_readiness` evaluates the whole NSE EQ universe: every requirement is expressed
as `covered == total`, so a single incomplete security blocks research on every other
security. `PROJECT_INTENT.md` requires the opposite behaviour:

    "Readiness must be evaluated per security and per agent ... Incomplete securities must
    not block otherwise complete securities."

This module reuses the *same* agent contracts rather than restating them. It builds an
`AgentDataCoverage` scoped to one security, where the universe size is 1, so every existing
`covered == total` check becomes "is this one security complete?". The agent requirements,
freshness rules and provenance rules stay in a single place and cannot drift.

Global provenance rules (no synthetic data, unapproved feeds disabled) remain universe-wide
and fail closed. Those are correctness rules about the corpus, not coverage rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.agent_data_readiness import (
    AgentDataCoverage,
    AgentReadinessReport,
    evaluate_agent_readiness,
)
from app.core.config import Settings
from app.core.data_readiness import DataCoverage
from app.core.financial_history_coverage import load_financial_history_coverage
from app.core.market_history_coverage import load_market_history_coverage
from app.core.peer_metric_coverage import load_peer_metric_coverage


class SecurityNotSupportedError(RuntimeError):
    """Raised when a security is not part of the supported NSE EQ universe."""

    def __init__(self, security_id: UUID) -> None:
        self.security_id = security_id
        super().__init__(f"Security {security_id} is not a supported NSE EQ security")


@dataclass(frozen=True)
class SecurityReadiness:
    """Readiness of every agent for one specific security."""

    security_id: UUID
    symbol: str
    report: AgentReadinessReport

    @property
    def ready(self) -> bool:
        return self.report.ready

    @property
    def blocking_agents(self) -> tuple[str, ...]:
        return self.report.blocking_agents

    def blockers(self) -> tuple[str, ...]:
        """Flat, de-duplicated list of every blocking reason for this security."""
        return tuple(
            dict.fromkeys(
                f"{item.agent.value}: {message}"
                for item in self.report.agents
                if not item.ready
                for message in item.errors
            )
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "security_id": str(self.security_id),
            "symbol": self.symbol,
            "ready": self.ready,
            "blocking_agents": list(self.blocking_agents),
            "blockers": list(self.blockers()),
            "agents": [item.as_dict() for item in self.report.agents],
        }


async def load_security_agent_coverage(
    engine: AsyncEngine,
    security_id: UUID,
) -> tuple[AgentDataCoverage, str]:
    """Build an AgentDataCoverage describing exactly one security (universe size 1)."""
    statement = text(
        """
        with target as (
          select id, nse_symbol, sector, industry, metadata
          from securities
          where id = :security_id
            and primary_exchange = 'NSE'
            and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
        ), recent_filings as (
          select distinct src.security_id
          from sources src
          join target n on n.id = src.security_id
          join evidence_chunks ec on ec.source_id = src.id
          where src.source_type in ('exchange_filing', 'company_filing', 'regulator')
            and length(btrim(ec.content)) > 0
            and coalesce(src.published_at, src.retrieved_at) >= now() - interval '400 days'
        ), recent_earnings as (
          select distinct ce.security_id
          from corporate_events ce
          join target n on n.id = ce.security_id
          join corporate_event_sources ces on ces.event_id = ce.id
          join evidence_chunks ec on ec.source_id = ces.source_id
          join sources src on src.id = ces.source_id
          where ces.parse_status = 'parsed'
            and length(btrim(ec.content)) > 0
            and (
              ce.event_type in (
                'financial_results', 'earnings_call', 'earnings_transcript',
                'investor_presentation'
              )
              or ces.document_role in ('transcript', 'presentation', 'xbrl')
            )
            and coalesce(src.published_at, ce.event_at, src.retrieved_at)
                >= now() - interval '220 days'
        )
        select
          (select count(*) from target) as universe,
          (select max(nse_symbol) from target) as symbol,
          (
            select count(distinct pi.security_id)
            from provider_instruments pi join target n on n.id = pi.security_id
          ) as provider_mapped_securities,
          (
            select count(*)
            from target n
            where nullif(btrim(coalesce(n.sector, '')), '') is not null
              and nullif(btrim(coalesce(n.industry, '')), '') is not null
              and n.metadata->>'classification_taxonomy' = 'NSE_INDICES_4_TIER'
              and n.metadata->>'classification_provenance_class' = 'official_source'
              and n.metadata->>'classification_source_type' = 'nse_industry_classification'
              and nullif(btrim(coalesce(n.metadata->>'classification_sha256', '')), '') is not null
              and exists (
                select 1
                from sources src
                where src.id::text = n.metadata->>'classification_source_id'
                  and src.security_id = n.id
                  and src.source_type = 'nse_industry_classification'
                  and src.metadata->>'provenance_class' = 'official_source'
                  and coalesce(src.metadata->>'production_approved', 'false') = 'true'
                  and src.checksum = n.metadata->>'classification_sha256'
              )
          ) as classified_securities,
          (select count(*) from recent_filings) as recent_filing_evidence_securities,
          (select count(*) from recent_earnings) as recent_earnings_evidence_securities
        """
    )
    async with engine.connect() as connection:
        row = (await connection.execute(statement, {"security_id": security_id})).mappings().one()
        if int(row.get("universe") or 0) == 0:
            raise SecurityNotSupportedError(security_id)

        # Benchmarks and macro are shared market context, not per-security data.
        benchmark_rows = (
            await connection.execute(
                text(
                    """
                    select distinct b.code
                    from benchmark_bars bb
                    join benchmarks b on b.id = bb.benchmark_id
                    where bb.source_id is not null
                    """
                )
            )
        ).scalars().all()
        macro_rows = (
            await connection.execute(
                text(
                    """
                    select distinct series_key
                    from macro_observations
                    where source_id is not null
                    """
                )
            )
        ).scalars().all()

    financial_history = await load_financial_history_coverage(engine, security_id=security_id)
    market_history = await load_market_history_coverage(engine, security_id=security_id)
    peer_metrics = await load_peer_metric_coverage(engine, security_id=security_id)

    coverage = AgentDataCoverage(
        nse_eq_securities=1,
        provider_mapped_securities=int(row.get("provider_mapped_securities") or 0),
        classified_securities=int(row.get("classified_securities") or 0),
        financial_history_securities=financial_history.complete_securities,
        recent_filing_evidence_securities=int(row.get("recent_filing_evidence_securities") or 0),
        recent_earnings_evidence_securities=int(
            row.get("recent_earnings_evidence_securities") or 0
        ),
        technical_history_securities=market_history.complete_securities,
        peer_metric_securities=peer_metrics.complete_securities,
        benchmark_codes_with_sourced_bars=frozenset(str(v).upper() for v in benchmark_rows),
        macro_series_with_sourced_observations=frozenset(str(v) for v in macro_rows),
        history_limited_recent_securities=market_history.history_limited_recent_listings,
        financial_history_limited_recent_securities=(
            financial_history.history_limited_recent_listings
        ),
    )
    return coverage, str(row.get("symbol") or security_id)


def evaluate_security_readiness(
    security_id: UUID,
    symbol: str,
    security_coverage: AgentDataCoverage,
    corpus_coverage: DataCoverage,
    settings: Settings,
    *,
    as_of: datetime | None = None,
) -> SecurityReadiness:
    """Apply the shared 16-agent contract to a single security.

    `min_nse_eq_securities=1` because the universe here is deliberately this one security.
    Every other threshold, freshness rule and provenance rule is the production contract,
    unchanged.
    """
    report = evaluate_agent_readiness(
        security_coverage,
        corpus_coverage,
        settings,
        min_nse_eq_securities=1,
        as_of=as_of or datetime.now(UTC),
    )
    return SecurityReadiness(security_id=security_id, symbol=symbol, report=report)
