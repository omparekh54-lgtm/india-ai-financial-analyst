from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput
from app.agents.risk_agent import RiskRedFlagAgent


@pytest.mark.asyncio
async def test_risk_agent_surfaces_financial_and_governance_signals() -> None:
    result = await RiskRedFlagAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            context={
                "financial_metrics": {
                    "cfo_to_pat": 0.5,
                    "net_debt_to_ebitda": 4.2,
                    "interest_coverage": 1.3,
                },
                "governance": {"promoter_pledge_pct": 30},
            },
        )
    )

    assert result.metrics["signal_count"] == 4
    assert result.metrics["high_severity_count"] >= 3
    assert all(claim.status == "pending" for claim in result.claims)
