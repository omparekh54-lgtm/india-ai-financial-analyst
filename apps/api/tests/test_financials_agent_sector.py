from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, EvidenceRef
from app.agents.financials_agent import FinancialForensicAgent


@pytest.mark.asyncio
async def test_financial_agent_surfaces_banking_kpis_as_sourced_facts() -> None:
    evidence = EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://nsearchives.nseindia.com/corporate/bank-result.xbrl",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="periodic",
    )
    result = await FinancialForensicAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="HDFCBANK",
            evidence=[evidence],
            context={
                "financials": {
                    "net_interest_income": 32000,
                    "gross_npa_pct": 1.24,
                    "net_npa_pct": 0.33,
                    "nim_pct": 3.45,
                    "casa_ratio_pct": 38.2,
                    "gross_npa_pct_period_end": "2026-06-30",
                }
            },
        )
    )

    assert result.metrics["sector_kpis"] == {
        "net_interest_income": 32000.0,
        "gross_npa_pct": 1.24,
        "net_npa_pct": 0.33,
        "nim_pct": 3.45,
        "casa_ratio_pct": 38.2,
    }
    gross_npa_claim = next(
        claim for claim in result.claims if claim.data.get("metric") == "gross_npa_pct"
    )
    assert gross_npa_claim.claim_type == "fact"
    assert gross_npa_claim.data["period_end"] == "2026-06-30"
    assert gross_npa_claim.evidence_ids == [evidence.evidence_id]
