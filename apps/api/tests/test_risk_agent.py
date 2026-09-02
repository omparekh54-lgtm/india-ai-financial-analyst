from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, EvidenceRef
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


@pytest.mark.asyncio
async def test_risk_agent_grounds_auditor_resignation_in_exact_filing() -> None:
    now = datetime.now(UTC).isoformat()
    evidence = EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://nsearchives.nseindia.com/auditor.pdf",
        title="Auditor resignation filing",
        published_at=now,
        retrieved_at=now,
        freshness="near_live",
        excerpt="The statutory auditor has tendered its resignation effective today.",
        page_number=2,
        section="auditor_resignation",
        source_priority=1,
    )

    result = await RiskRedFlagAgent().run(
        AgentInput(job_id=uuid4(), query="EXAMPLE", evidence=[evidence])
    )

    filing_claims = [
        claim for claim in result.claims if claim.data.get("title") == "Auditor resignation filing"
    ]
    assert len(filing_claims) == 1
    assert filing_claims[0].evidence_ids == [evidence.evidence_id]
    assert filing_claims[0].data["page_number"] == 2
