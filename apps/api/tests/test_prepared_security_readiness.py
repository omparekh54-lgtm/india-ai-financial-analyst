from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from app.agents.contracts import AgentName
from app.core.agent_data_readiness import (
    AgentDataCoverage,
    AgentReadiness,
    AgentReadinessReport,
)
from app.core.prepared_security_readiness import (
    SECURITY_READINESS_RULE_VERSION,
    SecurityReadinessFreshness,
    build_prepared_agent_rows,
)
from app.core.security_readiness import SecurityReadiness


def _coverage() -> AgentDataCoverage:
    return AgentDataCoverage(
        nse_eq_securities=1,
        provider_mapped_securities=1,
        classified_securities=1,
        financial_history_securities=1,
        recent_filing_evidence_securities=1,
        recent_earnings_evidence_securities=1,
        technical_history_securities=1,
        peer_metric_securities=1,
        benchmark_codes_with_sourced_bars=frozenset({"NIFTY50", "INDIAVIX"}),
        macro_series_with_sourced_observations=frozenset(
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
    )


def test_prepared_rows_persist_every_agent_and_exact_blockers() -> None:
    security_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    agents = tuple(
        AgentReadiness(
            agent=agent,
            ready=agent is not AgentName.RISK,
            errors=(("source-backed risk input is incomplete",) if agent is AgentName.RISK else ()),
        )
        for agent in AgentName
    )
    readiness = SecurityReadiness(
        security_id=security_id,
        symbol="RELIANCE",
        report=AgentReadinessReport(coverage=_coverage(), agents=agents),
    )
    evaluated_at = datetime(2026, 9, 22, 4, 30)
    rows = build_prepared_agent_rows(
        readiness,
        SecurityReadinessFreshness(
            latest_market_session=date(2026, 9, 21),
            latest_financial_period=date(2026, 6, 30),
            latest_filing_at=datetime(2026, 7, 17, 19, 50, tzinfo=UTC),
            latest_earnings_at=datetime(2026, 7, 17, 19, 50, tzinfo=UTC),
        ),
        evaluated_at=evaluated_at,
    )

    assert len(rows) == len(AgentName) == 16
    assert {row["agent_name"] for row in rows} == {agent.value for agent in AgentName}
    assert all(row["security_id"] == security_id for row in rows)
    assert all(row["rule_version"] == SECURITY_READINESS_RULE_VERSION for row in rows)
    assert all(row["evaluated_at"] == evaluated_at.replace(tzinfo=UTC) for row in rows)
    assert all(row["blocking_agents"] == [AgentName.RISK.value] for row in rows)
    risk = next(row for row in rows if row["agent_name"] == AgentName.RISK.value)
    assert risk["ready"] is False
    assert risk["errors"] == ["source-backed risk input is incomplete"]
    assert risk["latest_financial_period"] == date(2026, 6, 30)


def test_readiness_migration_is_backend_only_and_fail_closed() -> None:
    migration = (
        Path(__file__).resolve().parents[3]
        / "database"
        / "migrations"
        / "0028_security_agent_readiness_status.sql"
    ).read_text(encoding="utf-8")

    assert "primary key (security_id, agent_name)" in migration
    assert "enable row level security" in migration
    assert "revoke all" in migration
    assert "to anon, authenticated" in migration
    assert "using (false) with check (false)" in migration
    assert "rule_version text not null" in migration
    assert "evaluated_at timestamptz not null" in migration
