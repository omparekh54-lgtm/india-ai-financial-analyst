from __future__ import annotations

from datetime import UTC, date, datetime

from app.agents.contracts import AgentName
from app.core.agent_data_readiness import AgentDataCoverage, evaluate_agent_readiness
from app.core.config import Settings
from app.core.data_readiness import DataCoverage

MACRO_SERIES = frozenset(
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


def _corpus(as_of: datetime) -> DataCoverage:
    return DataCoverage(
        nse_eq_securities=1000,
        provider_instruments=1000,
        nse_securities_with_financial_facts=1000,
        financial_facts=24000,
        sourced_financial_facts=24000,
        nse_securities_with_corporate_events=1000,
        corporate_events=12000,
        sourced_corporate_events=12000,
        sources=30000,
        nonproduction_sources=0,
        evidence_chunks=80000,
        embedded_evidence_chunks=80000,
        nse_securities_with_market_bars=1000,
        market_bars=250000,
        sourced_market_bars=250000,
        benchmark_bars=1000,
        sourced_benchmark_bars=1000,
        macro_observations=5000,
        sourced_macro_observations=5000,
        nse_securities_with_security_metrics=1000,
        security_metrics=6000,
        sourced_security_metrics=6000,
        enabled_official_feeds=2,
        enabled_unapproved_official_feeds=0,
        latest_financial_period=date(2026, 6, 30),
        latest_corporate_event=as_of,
        latest_market_bar=as_of,
        latest_benchmark_bar=as_of,
        latest_macro_observation=as_of.date(),
    )


def _agent_coverage(**overrides: object) -> AgentDataCoverage:
    values: dict[str, object] = {
        "nse_eq_securities": 1000,
        "provider_mapped_securities": 1000,
        "classified_securities": 1000,
        "financial_history_securities": 1000,
        "recent_filing_evidence_securities": 1000,
        "recent_earnings_evidence_securities": 1000,
        "technical_history_securities": 1000,
        "peer_metric_securities": 1000,
        "benchmark_codes_with_sourced_bars": frozenset({"NIFTY50", "INDIAVIX"}),
        "macro_series_with_sourced_observations": MACRO_SERIES,
    }
    values.update(overrides)
    return AgentDataCoverage(**values)  # type: ignore[arg-type]


def _settings(*, external_data: bool = True) -> Settings:
    return Settings(
        enable_external_data_calls=external_data,
        tavily_api_key="configured-credential" if external_data else None,
    )


def _by_agent(report: object) -> dict[AgentName, object]:
    return {item.agent: item for item in report.agents}  # type: ignore[attr-defined]


def test_all_agents_require_complete_real_data_contract() -> None:
    as_of = datetime(2026, 8, 31, tzinfo=UTC)
    report = evaluate_agent_readiness(
        _agent_coverage(),
        _corpus(as_of),
        _settings(),
        as_of=as_of,
    )

    assert report.ready is True
    assert report.blocking_agents == ()
    assert len(report.agents) == len(AgentName) == 16
    assert all(item.ready for item in report.agents)


def test_missing_earnings_coverage_blocks_earnings_and_downstream_composition() -> None:
    as_of = datetime(2026, 8, 31, tzinfo=UTC)
    report = evaluate_agent_readiness(
        _agent_coverage(recent_earnings_evidence_securities=999),
        _corpus(as_of),
        _settings(),
        as_of=as_of,
    )
    by_agent = _by_agent(report)

    assert by_agent[AgentName.EARNINGS].ready is False  # type: ignore[attr-defined]
    assert by_agent[AgentName.ORCHESTRATOR].ready is False  # type: ignore[attr-defined]
    assert by_agent[AgentName.SYNTHESIS].ready is False  # type: ignore[attr-defined]
    assert by_agent[AgentName.FINANCIALS].ready is True  # type: ignore[attr-defined]


def test_fresh_news_and_web_are_not_ready_without_approved_runtime_acquisition() -> None:
    as_of = datetime(2026, 8, 31, tzinfo=UTC)
    report = evaluate_agent_readiness(
        _agent_coverage(),
        _corpus(as_of),
        _settings(external_data=False),
        as_of=as_of,
    )
    by_agent = _by_agent(report)

    assert by_agent[AgentName.NEWS].ready is False  # type: ignore[attr-defined]
    assert by_agent[AgentName.WEB].ready is False  # type: ignore[attr-defined]
    assert by_agent[AgentName.SENTIMENT].ready is False  # type: ignore[attr-defined]
    assert by_agent[AgentName.RISK].ready is False  # type: ignore[attr-defined]
    assert "news_event_intelligence" in report.blocking_agents


def test_nonproduction_source_blocks_every_agent() -> None:
    as_of = datetime(2026, 8, 31, tzinfo=UTC)
    corpus = _corpus(as_of)
    unsafe = DataCoverage(**{**corpus.__dict__, "nonproduction_sources": 1})
    report = evaluate_agent_readiness(
        _agent_coverage(),
        unsafe,
        _settings(),
        as_of=as_of,
    )

    assert report.ready is False
    assert all(item.ready is False for item in report.agents)
    assert all(any("forbidden" in error for error in item.errors) for item in report.agents)


def test_recent_listing_history_limit_warns_without_falsely_blocking_ready_corpus() -> None:
    as_of = datetime(2026, 8, 31, tzinfo=UTC)
    report = evaluate_agent_readiness(
        _agent_coverage(history_limited_recent_securities=2),
        _corpus(as_of),
        _settings(),
        as_of=as_of,
    )
    by_agent = _by_agent(report)

    assert report.ready is True
    for agent in (AgentName.MARKET, AgentName.TECHNICAL, AgentName.VALUATION):
        assert by_agent[agent].ready is True  # type: ignore[attr-defined]
        assert any(
            "fewer than 30 sessions" in warning
            for warning in by_agent[agent].warnings  # type: ignore[attr-defined]
        )
