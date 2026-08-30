from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim


class ChiefAnalystAgent:
    """Composes a research payload strictly from claims admitted by Agent 15."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        raw_claims = agent_input.context.get("validated_claims") or []
        claims = [claim if isinstance(claim, Claim) else Claim.model_validate(claim) for claim in raw_claims]

        disallowed = [claim for claim in claims if claim.status not in {"verified", "supported", "inferred"}]
        if disallowed:
            return AgentOutput(
                agent=AgentName.SYNTHESIS,
                ok=False,
                errors=["Synthesis received claims that did not pass the validation gate"],
            )

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for claim in claims:
            grouped[claim.agent.value].append(claim.model_dump(mode="json"))

        confidence = _confidence_framework(claims)
        report = {
            "query": agent_input.query,
            "claim_count": len(claims),
            "sections": dict(grouped),
            "confidence": confidence,
            "validation": agent_input.context.get("validation_metrics", {}),
            "warnings": agent_input.context.get("validation_warnings", []),
            "research_disclaimer": (
                "Research intelligence output. It is not an execution instruction or a substitute "
                "for regulated investment advice."
            ),
        }

        return AgentOutput(
            agent=AgentName.SYNTHESIS,
            claims=claims,
            evidence=agent_input.evidence,
            metrics={"report": report, **confidence},
        )


def _confidence_framework(claims: list[Claim]) -> dict[str, float]:
    if not claims:
        return {
            "data_confidence": 0.0,
            "thesis_confidence": 0.0,
            "valuation_confidence": 0.0,
            "catalyst_confidence": 0.0,
        }

    def average(selected: list[Claim]) -> float:
        if not selected:
            return 0.0
        return round(sum(claim.confidence for claim in selected) / len(selected), 4)

    verified_or_supported = [
        claim for claim in claims if claim.status in {"verified", "supported"}
    ]
    thesis_claims = [
        claim
        for claim in claims
        if claim.agent
        in {
            AgentName.FINANCIALS,
            AgentName.EARNINGS,
            AgentName.INDUSTRY,
            AgentName.RISK,
            AgentName.SENTIMENT,
        }
    ]
    valuation_claims = [claim for claim in claims if claim.agent == AgentName.VALUATION]
    catalyst_claims = [
        claim
        for claim in claims
        if claim.claim_type == "catalyst" or claim.agent in {AgentName.NEWS, AgentName.EARNINGS}
    ]

    return {
        "data_confidence": average(verified_or_supported),
        "thesis_confidence": average(thesis_claims),
        "valuation_confidence": average(valuation_claims),
        "catalyst_confidence": average(catalyst_claims),
    }
