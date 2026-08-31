from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import AgentName
from app.core.config import Settings
from app.core.data_readiness import DataCoverage
from app.core.financial_history_coverage import load_financial_history_coverage
from app.core.market_history_coverage import load_market_history_coverage
from app.core.peer_metric_coverage import load_peer_metric_coverage

_REQUIRED_MACRO_SERIES = frozenset(
    {
        "repo_rate",
        "india_10y_yield",
        "usd_inr",
        "brent",
        "india_vix",
        "cpi_yoy",
        "iip_yoy",
        "fii_cash_net_cr",
        "dii_cash_net_cr",
    }
)
_REQUIRED_BENCHMARK_CODES = frozenset({"NIFTY50", "INDIAVIX"})
_LLM_ENRICHED_AGENTS = frozenset(
    {
        AgentName.FILINGS,
        AgentName.EARNINGS,
        AgentName.NEWS,
        AgentName.WEB,
        AgentName.INDUSTRY,
        AgentName.SENTIMENT,
        AgentName.RISK,
        AgentName.SYNTHESIS,
    }
)


@dataclass(frozen=True)
class AgentDataCoverage:
    nse_eq_securities: int
    provider_mapped_securities: int
    classified_securities: int
    financial_history_securities: int
    recent_filing_evidence_securities: int
    recent_earnings_evidence_securities: int
    technical_history_securities: int
    peer_metric_securities: int
    benchmark_codes_with_sourced_bars: frozenset[str]
    macro_series_with_sourced_observations: frozenset[str]
    history_limited_recent_securities: int = 0
    financial_history_limited_recent_securities: int = 0

    def as_dict(self) -> dict[str, object]:
        total = self.nse_eq_securities
        return {
            "nse_eq_securities": total,
            "provider_mapped_securities": self.provider_mapped_securities,
            "provider_mapping_coverage_pct": _coverage_pct(self.provider_mapped_securities, total),
            "classified_securities": self.classified_securities,
            "classification_coverage_pct": _coverage_pct(self.classified_securities, total),
            "financial_history_securities": self.financial_history_securities,
            "financial_history_coverage_pct": _coverage_pct(self.financial_history_securities, total),
            "financial_history_limited_recent_securities": (
                self.financial_history_limited_recent_securities
            ),
            "recent_filing_evidence_securities": self.recent_filing_evidence_securities,
            "recent_filing_coverage_pct": _coverage_pct(
                self.recent_filing_evidence_securities,
                total,
            ),
            "recent_earnings_evidence_securities": self.recent_earnings_evidence_securities,
            "recent_earnings_coverage_pct": _coverage_pct(
                self.recent_earnings_evidence_securities,
                total,
            ),
            "technical_history_securities": self.technical_history_securities,
            "technical_history_coverage_pct": _coverage_pct(
                self.technical_history_securities,
                total,
            ),
            "history_limited_recent_securities": self.history_limited_recent_securities,
            "peer_metric_securities": self.peer_metric_securities,
            "peer_metric_coverage_pct": _coverage_pct(self.peer_metric_securities, total),
            "benchmark_codes_with_sourced_bars": sorted(self.benchmark_codes_with_sourced_bars),
            "macro_series_with_sourced_observations": sorted(
                self.macro_series_with_sourced_observations
            ),
        }


@dataclass(frozen=True)
class AgentReadiness:
    agent: AgentName
    ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "agent": self.agent.value,
            "ready": self.ready,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class AgentReadinessReport:
    coverage: AgentDataCoverage
    agents: tuple[AgentReadiness, ...]

    @property
    def ready(self) -> bool:
        return all(item.ready for item in self.agents)

    @property
    def blocking_agents(self) -> tuple[str, ...]:
        return tuple(item.agent.value for item in self.agents if not item.ready)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "blocking_agents": list(self.blocking_agents),
            "coverage": self.coverage.as_dict(),
            "agents": [item.as_dict() for item in self.agents],
        }


async def load_agent_data_coverage(engine: AsyncEngine) -> AgentDataCoverage:
    """Measure only real, source-linked inputs required by the production agents.

    The thresholds intentionally describe a high-coverage production corpus rather than a
    development fixture. They never create, infer, or backfill missing data.
    """
    statement = text(
        """
        with nse_eq as (
          select id, sector, industry, metadata
          from securities
          where primary_exchange = 'NSE'
            and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
        ), recent_filings as (
          select distinct src.security_id
          from sources src
          join nse_eq n on n.id = src.security_id
          join evidence_chunks ec on ec.source_id = src.id
          where src.source_type in ('exchange_filing', 'company_filing', 'regulator')
            and length(btrim(ec.content)) > 0
            and coalesce(src.published_at, src.retrieved_at) >= now() - interval '400 days'
        ), recent_earnings as (
          select distinct ce.security_id
          from corporate_events ce
          join nse_eq n on n.id = ce.security_id
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
          (select count(*) from nse_eq) as nse_eq_securities,
          (
            select count(distinct pi.security_id)
            from provider_instruments pi join nse_eq n on n.id = pi.security_id
          ) as provider_mapped_securities,
          (
            select count(*)
            from nse_eq n
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
        row = (await connection.execute(statement)).mappings().one()
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

    financial_history = await load_financial_history_coverage(engine)
    market_history = await load_market_history_coverage(engine)
    peer_metrics = await load_peer_metric_coverage(engine)
    return AgentDataCoverage(
        nse_eq_securities=_int(row.get("nse_eq_securities")),
        provider_mapped_securities=_int(row.get("provider_mapped_securities")),
        classified_securities=_int(row.get("classified_securities")),
        financial_history_securities=financial_history.complete_securities,
        recent_filing_evidence_securities=_int(row.get("recent_filing_evidence_securities")),
        recent_earnings_evidence_securities=_int(row.get("recent_earnings_evidence_securities")),
        technical_history_securities=market_history.complete_securities,
        peer_metric_securities=peer_metrics.complete_securities,
        benchmark_codes_with_sourced_bars=frozenset(str(value).upper() for value in benchmark_rows),
        macro_series_with_sourced_observations=frozenset(str(value) for value in macro_rows),
        history_limited_recent_securities=market_history.history_limited_recent_listings,
        financial_history_limited_recent_securities=(
            financial_history.history_limited_recent_listings
        ),
    )


def evaluate_agent_readiness(
    agent_coverage: AgentDataCoverage,
    corpus_coverage: DataCoverage,
    settings: Settings,
    *,
    min_nse_eq_securities: int = 1000,
    as_of: datetime | None = None,
) -> AgentReadinessReport:
    """Build the production data contract for all 16 logical research roles.

    A role is green only when its required real-data inputs are complete for the supported
    universe. Optional LLM enrichment is not required because each specialist has a
    deterministic implementation. News and Web do require approved fresh-data acquisition.
    """
    now = _utc(as_of or datetime.now(UTC))
    total = agent_coverage.nse_eq_securities
    global_errors = _global_provenance_errors(corpus_coverage)

    def errors_for(*requirements: tuple[bool, str]) -> tuple[str, ...]:
        errors = list(global_errors)
        errors.extend(message for condition, message in requirements if not condition)
        return tuple(dict.fromkeys(errors))

    def warnings_for(agent: AgentName) -> tuple[str, ...]:
        warnings = list(_agent_warnings(agent, settings))
        if (
            agent in {AgentName.MARKET, AgentName.TECHNICAL, AgentName.VALUATION}
            and agent_coverage.history_limited_recent_securities > 0
        ):
            warnings.append(
                f"{agent_coverage.history_limited_recent_securities} recent NSE listing(s) have "
                "complete available market history but fewer than 30 sessions; technical indicators "
                "must report a listing-age limitation for those securities."
            )
        if (
            agent
            in {
                AgentName.FINANCIALS,
                AgentName.EARNINGS,
                AgentName.VALUATION,
                AgentName.RISK,
            }
            and agent_coverage.financial_history_limited_recent_securities > 0
        ):
            warnings.append(
                f"{agent_coverage.financial_history_limited_recent_securities} recent NSE listing(s) "
                "have complete available post-listing financial history but fewer than eight "
                "reporting periods; analysis must state the listing-age limitation."
            )
        return tuple(warnings)

    universe_ready = total >= min_nse_eq_securities
    mapped_ready = total > 0 and agent_coverage.provider_mapped_securities == total
    classified_ready = total > 0 and agent_coverage.classified_securities == total
    financial_ready = total > 0 and agent_coverage.financial_history_securities == total
    filing_ready = total > 0 and agent_coverage.recent_filing_evidence_securities == total
    earnings_ready = total > 0 and agent_coverage.recent_earnings_evidence_securities == total
    technical_ready = total > 0 and agent_coverage.technical_history_securities == total
    peer_ready = total > 0 and agent_coverage.peer_metric_securities == total
    benchmarks_ready = _REQUIRED_BENCHMARK_CODES <= agent_coverage.benchmark_codes_with_sourced_bars
    macro_ready = _REQUIRED_MACRO_SERIES <= agent_coverage.macro_series_with_sourced_observations
    market_fresh = _datetime_age_days(now, corpus_coverage.latest_market_bar) <= 7
    benchmark_fresh = _datetime_age_days(now, corpus_coverage.latest_benchmark_bar) <= 7
    macro_fresh = _date_age_days(now, corpus_coverage.latest_macro_observation) <= 45
    web_acquisition_ready = bool(settings.enable_external_data_calls and settings.tavily_api_key)

    agent_errors: dict[AgentName, tuple[str, ...]] = {}
    agent_errors[AgentName.ENTITY] = errors_for(
        (universe_ready, f"Full NSE EQ universe required: {total} < {min_nse_eq_securities}."),
        (
            mapped_ready,
            "Every supported NSE EQ security must have at least one provider-instrument mapping.",
        ),
    )
    agent_errors[AgentName.MARKET] = errors_for(
        (mapped_ready, "Provider-instrument mapping coverage must be 100%."),
        (
            technical_ready,
            (
                "Every supported security needs complete listing-age-aware sourced daily history: "
                "200 bars for mature listings and complete available history for recent listings."
            ),
        ),
        (benchmarks_ready, "Sourced NIFTY 50 and India VIX histories are required."),
        (market_fresh, "Security market history is stale beyond 7 days."),
        (benchmark_fresh, "Benchmark history is stale beyond 7 days."),
    )
    agent_errors[AgentName.FINANCIALS] = errors_for(
        (
            financial_ready,
            (
                "Every supported security needs complete listing-age-aware sourced financial "
                "history, at least 6 canonical fact types, and a latest period within 200 days."
            ),
        ),
    )
    agent_errors[AgentName.FILINGS] = errors_for(
        (
            filing_ready,
            "Every supported security needs parsed primary filing evidence from the last 400 days.",
        ),
    )
    agent_errors[AgentName.EARNINGS] = errors_for(
        (financial_ready, "Earnings analysis requires complete financial-history coverage."),
        (
            earnings_ready,
            (
                "Every supported security needs parsed results/call/transcript/presentation evidence "
                "from the last 220 days."
            ),
        ),
    )
    agent_errors[AgentName.NEWS] = errors_for(
        (
            web_acquisition_ready,
            "Fresh News Agent coverage requires approved external-data calls and Tavily credentials.",
        ),
    )
    agent_errors[AgentName.WEB] = errors_for(
        (
            web_acquisition_ready,
            "Fresh Web Agent coverage requires approved external-data calls and Tavily credentials.",
        ),
    )
    agent_errors[AgentName.INDUSTRY] = errors_for(
        (
            classified_ready,
            "Every supported security needs provenance-linked official NSE sector and industry classification.",
        ),
        (
            peer_ready,
            (
                "Every supported security needs at least 3 recent, auditable comparable metrics "
                "used by the Industry Agent."
            ),
        ),
    )
    agent_errors[AgentName.MACRO] = errors_for(
        (
            macro_ready,
            "Macro Agent requires all nine sourced series: "
            + ", ".join(sorted(_REQUIRED_MACRO_SERIES)),
        ),
        (macro_fresh, "Macro/flow observations are stale beyond 45 days."),
        (benchmarks_ready, "Macro context requires sourced NIFTY 50 and India VIX history."),
    )
    agent_errors[AgentName.VALUATION] = errors_for(
        (financial_ready, "Valuation requires complete financial-history coverage."),
        (peer_ready, "Valuation requires complete recent peer/security metric coverage."),
        (technical_ready, "Valuation requires complete sourced market-history coverage."),
    )
    agent_errors[AgentName.TECHNICAL] = errors_for(
        (
            technical_ready,
            (
                "Technical analysis requires complete listing-age-aware sourced daily history: "
                "200 bars for mature listings and complete available history for recent listings."
            ),
        ),
        (benchmarks_ready, "Technical context requires sourced NIFTY 50 and India VIX history."),
        (market_fresh, "Technical market history is stale beyond 7 days."),
    )
    agent_errors[AgentName.SENTIMENT] = errors_for(
        (
            web_acquisition_ready,
            "Sentiment requires fresh approved News/Web acquisition rather than fabricated narrative data.",
        ),
    )
    agent_errors[AgentName.RISK] = errors_for(
        (financial_ready, "Risk analysis requires complete financial-history coverage."),
        (filing_ready, "Risk analysis requires complete recent primary filing coverage."),
        (
            web_acquisition_ready,
            "Risk event coverage requires fresh approved News/Web acquisition.",
        ),
    )
    validator_errors = errors_for(
        (corpus_coverage.sources > 0, "Evidence validator requires source rows."),
        (corpus_coverage.evidence_chunks > 0, "Evidence validator requires parsed evidence chunks."),
    )
    agent_errors[AgentName.VALIDATOR] = validator_errors

    specialists = (
        AgentName.ENTITY,
        AgentName.MARKET,
        AgentName.FINANCIALS,
        AgentName.FILINGS,
        AgentName.EARNINGS,
        AgentName.NEWS,
        AgentName.WEB,
        AgentName.INDUSTRY,
        AgentName.MACRO,
        AgentName.VALUATION,
        AgentName.TECHNICAL,
        AgentName.SENTIMENT,
        AgentName.RISK,
        AgentName.VALIDATOR,
    )
    downstream_errors = tuple(
        f"{agent.value} is not data-ready" for agent in specialists if agent_errors[agent]
    )
    propagated_downstream_errors = tuple(dict.fromkeys((*global_errors, *downstream_errors)))
    agent_errors[AgentName.ORCHESTRATOR] = propagated_downstream_errors
    agent_errors[AgentName.SYNTHESIS] = propagated_downstream_errors

    agents = tuple(
        AgentReadiness(
            agent=agent,
            ready=not agent_errors[agent],
            errors=agent_errors[agent],
            warnings=warnings_for(agent),
        )
        for agent in AgentName
    )
    return AgentReadinessReport(coverage=agent_coverage, agents=agents)


def _global_provenance_errors(coverage: DataCoverage) -> tuple[str, ...]:
    errors: list[str] = []
    if coverage.nonproduction_sources > 0:
        errors.append(
            f"Synthetic/mock/sample provenance is forbidden: {coverage.nonproduction_sources} "
            "non-production source row(s) detected."
        )
    material_tables = (
        ("financial facts", coverage.sourced_financial_facts, coverage.financial_facts),
        ("corporate events", coverage.sourced_corporate_events, coverage.corporate_events),
        ("market bars", coverage.sourced_market_bars, coverage.market_bars),
        ("benchmark bars", coverage.sourced_benchmark_bars, coverage.benchmark_bars),
        ("macro observations", coverage.sourced_macro_observations, coverage.macro_observations),
        ("security metrics", coverage.sourced_security_metrics, coverage.security_metrics),
    )
    for label, sourced, total in material_tables:
        if total > 0 and sourced != total:
            errors.append(f"All {label} must be source-linked: {sourced}/{total} have provenance.")
    if coverage.enabled_unapproved_official_feeds > 0:
        errors.append(
            "Production cannot use enabled exchange feeds that still require licensing/source approval."
        )
    return tuple(errors)


def _agent_warnings(agent: AgentName, settings: Settings) -> tuple[str, ...]:
    warnings: list[str] = []
    if agent in _LLM_ENRICHED_AGENTS and not settings.enable_external_llm_calls:
        warnings.append(
            "Optional LLM enrichment is disabled; deterministic agent logic remains available."
        )
    if agent == AgentName.MARKET and not settings.enable_live_market:
        warnings.append(
            "Live broker overlay is disabled; sourced stored market history remains the fallback."
        )
    return tuple(warnings)


def _coverage_pct(covered: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((covered / total) * 100.0, 2)


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _datetime_age_days(now: datetime, value: datetime | None) -> int:
    if value is None:
        return 10**9
    return max((now - _utc(value)).days, 0)


def _date_age_days(now: datetime, value: date | None) -> int:
    if value is None:
        return 10**9
    return max((now.date() - value).days, 0)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
