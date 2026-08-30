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
