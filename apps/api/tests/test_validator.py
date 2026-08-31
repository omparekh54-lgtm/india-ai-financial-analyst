from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, AgentName, Claim, EvidenceRef
from app.agents.validator_agent import EvidenceCrossValidationAgent


@pytest.mark.asyncio
async def test_validator_promotes_primary_sourced_claim() -> None:
    evidence = EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://example.test/filing",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
    )
    claim = Claim(
        agent=AgentName.FINANCIALS,
        statement="Revenue growth calculated as 0.1200",
        claim_type="calculation",
        confidence=0.9,
        evidence_ids=[evidence.evidence_id],
    )
    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            evidence=[evidence],
            context={"candidate_claims": [claim]},
        )
    )

    assert result.claims[0].status == "verified"
    assert result.claims[0].source_tier == "C"
    assert result.metrics["evidence_coverage"] == 1.0


@pytest.mark.asyncio
async def test_validator_keeps_grounded_interpretation_inferred() -> None:
    evidence = EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://example.test/management-commentary",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
    )
    claim = Claim(
        agent=AgentName.SENTIMENT,
        statement="Management narrative appears more cautious",
        claim_type="inference",
        confidence=0.72,
        evidence_ids=[evidence.evidence_id],
    )
    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            evidence=[evidence],
            context={"candidate_claims": [claim]},
        )
    )

    assert result.claims[0].status == "inferred"
    assert result.claims[0].confidence >= 0.8


@pytest.mark.asyncio
async def test_validator_rejects_unsourced_fact() -> None:
    claim = Claim(
        agent=AgentName.NEWS,
        statement="Unsupported material fact",
        claim_type="fact",
        confidence=0.9,
    )
    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            context={"candidate_claims": [claim]},
        )
    )

    assert result.ok is False
    assert result.claims[0].status == "unsupported"
    assert result.metrics["repair_tasks"]


@pytest.mark.asyncio
async def test_validator_recomputes_supported_calculation() -> None:
    evidence = EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://example.test/result",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
        source_priority=1,
    )
    claim = Claim(
        agent=AgentName.FINANCIALS,
        statement="CFO/PAT is 1.25",
        claim_type="calculation",
        confidence=0.9,
        evidence_ids=[evidence.evidence_id],
        metric="cfo_pat",
        value=1.25,
        period="FY26",
        calculation_version="financial.cfo_pat.v1",
        data={
            "calculation": {
                "operation": "ratio",
                "numerator": 125.0,
                "denominator": 100.0,
            }
        },
    )
    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            evidence=[evidence],
            context={"candidate_claims": [claim]},
        )
    )

    assert result.ok is True
    assert result.claims[0].status == "verified"
    assert result.claims[0].data["validator_recomputed"] is True
    assert result.metrics["recomputed_count"] == 1


@pytest.mark.asyncio
async def test_validator_contests_recomputation_mismatch() -> None:
    evidence = EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://example.test/result",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
        source_priority=1,
    )
    claim = Claim(
        agent=AgentName.FINANCIALS,
        statement="CFO/PAT is 1.50",
        claim_type="calculation",
        confidence=0.9,
        evidence_ids=[evidence.evidence_id],
        metric="cfo_pat",
        value=1.50,
        period="FY26",
        data={
            "calculation": {
                "operation": "ratio",
                "numerator": 125.0,
                "denominator": 100.0,
            }
        },
    )
    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            evidence=[evidence],
            context={"candidate_claims": [claim]},
        )
    )

    assert result.ok is False
    assert result.claims[0].status == "contested"
    assert result.metrics["repair_tasks"]


@pytest.mark.asyncio
async def test_validator_does_not_average_conflicting_claims() -> None:
    primary = EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://example.test/primary",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
        source_priority=1,
    )
    secondary = EvidenceRef(
        source_type="news",
        source_uri="https://example.test/secondary",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
        source_priority=3,
    )
    strong_claim = Claim(
        agent=AgentName.FINANCIALS,
        statement="Revenue is 100",
        claim_type="fact",
        confidence=0.9,
        evidence_ids=[primary.evidence_id],
        metric="revenue",
        value=100.0,
        unit="INR crore",
        period="Q1FY27",
    )
    weak_claim = Claim(
        agent=AgentName.NEWS,
        statement="Revenue is 95",
        claim_type="fact",
        confidence=0.8,
        evidence_ids=[secondary.evidence_id],
        metric="revenue",
        value=95.0,
        unit="INR crore",
        period="Q1FY27",
    )

    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            evidence=[primary, secondary],
            context={"candidate_claims": [strong_claim, weak_claim]},
        )
    )

    by_agent = {claim.agent: claim for claim in result.claims}
    assert by_agent[AgentName.FINANCIALS].status == "verified"
    assert by_agent[AgentName.NEWS].status == "contested"
    assert result.metrics["contradiction_count"] == 1
