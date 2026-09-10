"""Per-security readiness contract.

The central requirement from PROJECT_INTENT.md: "Incomplete securities must not block
otherwise complete securities." The previous universe-wide gate violated this, which is why
production served 503 for every request while holding usable data for some stocks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.agents.contracts import AgentName
from app.core.agent_data_readiness import AgentDataCoverage
from app.core.config import Settings
from app.core.data_readiness import DataCoverage
from app.core.security_readiness import evaluate_security_readiness

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "enable_external_data_calls": True,
        "enable_external_llm_calls": False,
        "tavily_api_key": "test-key",
    }
    base.update(overrides)
    return Settings(**base)


def _complete_coverage(**overrides: object) -> AgentDataCoverage:
    """A single security with every per-security requirement satisfied."""
    base: dict[str, object] = {
        "nse_eq_securities": 1,
        "provider_mapped_securities": 1,
        "classified_securities": 1,
        "financial_history_securities": 1,
        "recent_filing_evidence_securities": 1,
        "recent_earnings_evidence_securities": 1,
        "technical_history_securities": 1,
        "peer_metric_securities": 1,
        "benchmark_codes_with_sourced_bars": frozenset({"NIFTY50", "INDIAVIX"}),
        "macro_series_with_sourced_observations": frozenset(
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
        ),
    }
    base.update(overrides)
    return AgentDataCoverage(**base)  # type: ignore[arg-type]


def _corpus(**overrides: object) -> DataCoverage:
    fresh = NOW - timedelta(days=1)
    base: dict[str, object] = {
        "nse_eq_securities": 2302,
        "provider_instruments": 2302,
        "nse_securities_with_financial_facts": 1,
        "financial_facts": 96,
        "sourced_financial_facts": 96,
        "nse_securities_with_corporate_events": 1,
        "corporate_events": 1,
        "sourced_corporate_events": 1,
        "sources": 3943,
        "nonproduction_sources": 0,
        "evidence_chunks": 500,
        "embedded_evidence_chunks": 500,
        "nse_securities_with_market_bars": 2060,
        "market_bars": 700000,
        "sourced_market_bars": 700000,
        "benchmark_bars": 5000,
        "sourced_benchmark_bars": 5000,
        "macro_observations": 78,
        "sourced_macro_observations": 78,
        "nse_securities_with_security_metrics": 5,
        "security_metrics": 33,
        "sourced_security_metrics": 33,
        "enabled_official_feeds": 2,
        "enabled_unapproved_official_feeds": 0,
        "latest_market_bar": fresh,
        "latest_benchmark_bar": fresh,
        "latest_macro_observation": fresh.date(),
    }
    base.update(overrides)
    return DataCoverage(**base)  # type: ignore[arg-type]


def test_complete_security_is_ready_even_though_universe_is_incomplete() -> None:
    """The regression that mattered: a corpus covering 1/2302 stocks must not block that 1."""
    readiness = evaluate_security_readiness(
        uuid4(),
        "RELIANCE",
        _complete_coverage(),
        _corpus(),
        _settings(),
        as_of=NOW,
    )
    assert readiness.ready, readiness.blockers()
    assert readiness.blocking_agents == ()


def test_incomplete_security_is_blocked_with_named_agents() -> None:
    readiness = evaluate_security_readiness(
        uuid4(),
        "SOMESMALLCAP",
        _complete_coverage(
            financial_history_securities=0,
            recent_filing_evidence_securities=0,
        ),
        _corpus(),
        _settings(),
        as_of=NOW,
    )
    assert not readiness.ready
    assert AgentName.FINANCIALS.value in readiness.blocking_agents
    assert AgentName.FILINGS.value in readiness.blocking_agents
    # Downstream roles must propagate rather than silently proceed.
    assert AgentName.SYNTHESIS.value in readiness.blocking_agents


def test_one_incomplete_security_does_not_affect_another() -> None:
    """Two securities evaluated independently must not influence each other."""
    ready = evaluate_security_readiness(
        uuid4(), "TCS", _complete_coverage(), _corpus(), _settings(), as_of=NOW
    )
    blocked = evaluate_security_readiness(
        uuid4(),
        "ILLIQUIDCO",
        _complete_coverage(technical_history_securities=0),
        _corpus(),
        _settings(),
        as_of=NOW,
    )
    assert ready.ready
    assert not blocked.ready


def test_blockers_are_specific_and_actionable() -> None:
    readiness = evaluate_security_readiness(
        uuid4(),
        "NEWLISTING",
        _complete_coverage(technical_history_securities=0),
        _corpus(),
        _settings(),
        as_of=NOW,
    )
    blockers = readiness.blockers()
    assert blockers, "a blocked security must explain why"
    assert any("history" in message.lower() for message in blockers)
    assert all(":" in message for message in blockers), "blockers name their agent"


def test_synthetic_provenance_still_fails_closed_per_security() -> None:
    """Global corpus correctness rules are NOT relaxed by per-security scoping."""
    readiness = evaluate_security_readiness(
        uuid4(),
        "RELIANCE",
        _complete_coverage(),
        _corpus(nonproduction_sources=3),
        _settings(),
        as_of=NOW,
    )
    assert not readiness.ready
    assert any("synthetic" in message.lower() for message in readiness.blockers())


def test_stale_market_data_blocks_the_security() -> None:
    stale = NOW - timedelta(days=40)
    readiness = evaluate_security_readiness(
        uuid4(),
        "RELIANCE",
        _complete_coverage(),
        _corpus(latest_market_bar=stale, latest_benchmark_bar=stale),
        _settings(),
        as_of=NOW,
    )
    assert not readiness.ready
    assert any("stale" in message.lower() for message in readiness.blockers())


def test_missing_tavily_blocks_only_acquisition_agents() -> None:
    readiness = evaluate_security_readiness(
        uuid4(),
        "RELIANCE",
        _complete_coverage(),
        _corpus(),
        _settings(tavily_api_key=None),
        as_of=NOW,
    )
    assert not readiness.ready
    assert AgentName.NEWS.value in readiness.blocking_agents
    assert AgentName.WEB.value in readiness.blocking_agents
    assert AgentName.SENTIMENT.value in readiness.blocking_agents
    # Deterministic roles that need no external acquisition stay green.
    assert AgentName.TECHNICAL.value not in readiness.blocking_agents
    assert AgentName.MACRO.value not in readiness.blocking_agents


def test_as_dict_is_serialisable_for_the_api() -> None:
    readiness = evaluate_security_readiness(
        uuid4(), "TCS", _complete_coverage(), _corpus(), _settings(), as_of=NOW
    )
    payload = readiness.as_dict()
    assert payload["ready"] is True
    assert payload["symbol"] == "TCS"
    assert len(payload["agents"]) == len(AgentName)
