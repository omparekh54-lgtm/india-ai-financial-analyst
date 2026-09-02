from __future__ import annotations

from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, AgentName, Claim
from app.agents.synthesis_agent import ChiefAnalystAgent


def _claim(
    agent: AgentName,
    statement: str,
    claim_type: str,
    *,
    confidence: float = 0.9,
    data: dict[str, object] | None = None,
) -> Claim:
    return Claim(
        agent=agent,
        statement=statement,
        claim_type=claim_type,  # type: ignore[arg-type]
        confidence=confidence,
        status="supported",
        data=data or {},
    )


@pytest.mark.asyncio
async def test_chief_analyst_has_deterministic_thesis_without_llm() -> None:
    claims = [
        _claim(
            AgentName.FINANCIALS,
            "Operating margin expanded versus the prior reported period.",
            "fact",
        ),
        _claim(
            AgentName.NEWS,
            "A new capacity commissioning is a potential catalyst.",
            "catalyst",
        ),
        _claim(
            AgentName.RISK,
            "Receivable days increased materially.",
            "risk",
        ),
    ]
    output = await ChiefAnalystAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="Analyze company",
            context={
                "validated_claims": [claim.model_dump(mode="json") for claim in claims],
                "analysis_mode": "full_analysis",
                "analysis_depth": "standard",
            },
        )
    )

    report = output.metrics["report"]
    assert report["executive_summary"]
    assert report["investment_thesis"] == [claims[0].statement]
    assert report["catalysts"] == [claims[1].statement]
    assert report["thesis_breakers"] == [claims[2].statement]
    assert report["narrative"]["provider"] == "deterministic"


@pytest.mark.asyncio
async def test_chief_analyst_preserves_valuation_scenarios_without_inventing_probabilities() -> None:
    valuation = _claim(
        AgentName.VALUATION,
        "Bear/base/bull values calculated using price_to_book",
        "scenario",
        data={
            "method": "price_to_book",
            "method_code": "pb",
            "sector_family": "bank",
            "scenarios": {"bear": 170.0, "base": 200.0, "bull": 230.0},
            "upside_pct": 11.1,
        },
    )
    output = await ChiefAnalystAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="Analyze bank",
            context={"validated_claims": [valuation.model_dump(mode="json")]},
        )
    )

    valuation_payload = output.metrics["report"]["valuation_scenarios"]
    assert valuation_payload["scenarios"]["base"] == pytest.approx(200.0)
    assert valuation_payload["probability_weighted_value"] is None
    assert "does not invent" in valuation_payload["probability_note"]
