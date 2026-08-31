from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.research.insights import special_mode_insights

_STATUS_WEIGHT = {"verified": 1.0, "supported": 0.86, "inferred": 0.62}
_SOURCE_TIER_WEIGHT = {"A": 1.0, "B": 0.92, "C": 0.78, "D": 0.58, "E": 0.38}


class ChiefAnalystAgent:
    """Composes a research payload strictly from claims admitted by Agent 15."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        raw_claims = agent_input.context.get("validated_claims") or []
        claims = [
            claim if isinstance(claim, Claim) else Claim.model_validate(claim)
            for claim in raw_claims
        ]

        disallowed = [
            claim
            for claim in claims
            if claim.status not in {"verified", "supported", "inferred"}
        ]
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
        mode = str(agent_input.context.get("analysis_mode") or "full_analysis")
        depth = str(agent_input.context.get("analysis_depth") or "standard")
        special = special_mode_insights(
            mode,
            agent_input.context,
            dict(grouped),
            current_confidence=confidence,
        )
        evidence_catalog = {
            str(item.evidence_id): item.model_dump(mode="json")
            for item in agent_input.evidence
        }
        fallback = _deterministic_synthesis(claims, confidence)
        evidence_map = _thesis_evidence_map(claims)
        report = {
            "query": agent_input.query,
            "mode": mode,
            "depth": depth,
            "security": agent_input.context.get("security"),
            "claim_count": len(claims),
            "sections": dict(grouped),
            "special_mode": special,
            "evidence_catalog": evidence_catalog,
            "confidence": confidence,
            "executive_summary": fallback["executive_summary"],
            "narrative": fallback["narrative"],
            "investment_thesis": fallback["investment_thesis"],
            "anti_thesis": fallback["anti_thesis"],
            "catalysts": fallback["catalysts"],
            "thesis_breakers": fallback["thesis_breakers"],
            "valuation_scenarios": fallback["valuation_scenarios"],
            "thesis_evidence_map": evidence_map,
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


def _deterministic_synthesis(
    claims: list[Claim],
    confidence: dict[str, float],
) -> dict[str, object]:
    ranked = sorted(claims, key=_claim_rank, reverse=True)
    thesis_agents = {
        AgentName.FINANCIALS,
        AgentName.EARNINGS,
        AgentName.INDUSTRY,
        AgentName.VALUATION,
    }
    thesis = _unique_statements(
        [
            claim
            for claim in ranked
            if claim.agent in thesis_agents
            and claim.claim_type not in {"risk", "catalyst"}
            and not _needs_corroboration(claim)
        ],
        limit=4,
    )
    catalysts = _unique_statements(
        [
            claim
            for claim in ranked
            if claim.claim_type == "catalyst" and not _needs_corroboration(claim)
        ],
        limit=5,
    )
    risks = _unique_statements(
        [claim for claim in ranked if claim.claim_type == "risk"],
        limit=5,
    )
    watch_items = _unique_statements(
        [
            claim
            for claim in ranked
            if _needs_corroboration(claim)
            or claim.status == "inferred"
            or (
                claim.agent in {AgentName.FILINGS, AgentName.EARNINGS, AgentName.NEWS}
                and claim.claim_type not in {"risk", "catalyst"}
            )
        ],
        limit=5,
    )
    summary_source = thesis or catalysts or risks or _unique_statements(ranked, limit=3)
    if summary_source:
        executive_summary = "Validated evidence currently highlights: " + " ".join(summary_source[:3])
    else:
        executive_summary = (
            "No validated material claims were available for a research thesis in this run."
        )

    valuation_scenarios: dict[str, object] | None = None
    for claim in ranked:
        if claim.agent != AgentName.VALUATION:
            continue
        scenarios = claim.data.get("scenarios")
        if isinstance(scenarios, dict):
            valuation_scenarios = {
                "method": claim.data.get("method"),
                "method_code": claim.data.get("method_code"),
                "sector_family": claim.data.get("sector_family"),
                "scenarios": scenarios,
                "upside_pct": claim.data.get("upside_pct"),
                "probability_weighted_value": None,
                "probability_note": (
                    "No scenario probabilities were supplied, so the system does not invent "
                    "a probability-weighted target."
                ),
            }
            break

    confidence_note = (
        f"Data {confidence['data_confidence']:.0%}; thesis {confidence['thesis_confidence']:.0%}; "
        f"valuation {confidence['valuation_confidence']:.0%}; catalyst "
        f"{confidence['catalyst_confidence']:.0%}."
    )
    return {
        "executive_summary": executive_summary,
        "investment_thesis": thesis,
        "anti_thesis": risks,
        "catalysts": catalysts,
        "thesis_breakers": risks,
        "valuation_scenarios": valuation_scenarios,
        "narrative": {
            "bull_case": catalysts,
            "bear_case": risks,
            "watch_items": watch_items,
            "confidence_note": confidence_note,
            "provider": "deterministic",
            "model": "validated_claims_v2",
        },
    }


def _claim_rank(claim: Claim) -> tuple[int, float, float]:
    status_rank = {"verified": 3, "supported": 2, "inferred": 1}
    corroboration_penalty = 0.0 if _needs_corroboration(claim) else 1.0
    return status_rank.get(claim.status, 0), corroboration_penalty, _claim_quality(claim)


def _unique_statements(claims: list[Claim], *, limit: int) -> list[str]:
    statements: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        statement = claim.statement.strip()
        key = " ".join(statement.lower().split())
        if not statement or key in seen:
            continue
        seen.add(key)
        statements.append(statement)
        if len(statements) >= limit:
            break
    return statements


def _confidence_framework(claims: list[Claim]) -> dict[str, float]:
    if not claims:
        return {
            "data_confidence": 0.0,
            "thesis_confidence": 0.0,
            "valuation_confidence": 0.0,
            "catalyst_confidence": 0.0,
        }

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
        "data_confidence": _weighted_confidence(verified_or_supported),
        "thesis_confidence": _weighted_confidence(thesis_claims),
        "valuation_confidence": _weighted_confidence(valuation_claims),
        "catalyst_confidence": _weighted_confidence(catalyst_claims),
    }


def _weighted_confidence(claims: list[Claim]) -> float:
    if not claims:
        return 0.0
    weighted_total = 0.0
    denominator = 0.0
    for claim in claims:
        materiality = claim.materiality if claim.materiality is not None else 0.60
        materiality_weight = max(0.25, materiality)
        denominator += materiality_weight
        weighted_total += _claim_quality(claim) * materiality_weight
    return round(weighted_total / denominator, 4) if denominator else 0.0


def _claim_quality(claim: Claim) -> float:
    status_weight = _STATUS_WEIGHT.get(claim.status, 0.45)
    source_weight = _SOURCE_TIER_WEIGHT.get(str(claim.source_tier or ""), 0.62)
    corroboration_weight = 0.72 if _needs_corroboration(claim) else 1.0
    return max(
        0.0,
        min(1.0, claim.confidence * status_weight * source_weight * corroboration_weight),
    )


def _needs_corroboration(claim: Claim) -> bool:
    return claim.data.get("validator_needs_corroboration") is True


def _thesis_evidence_map(claims: list[Claim]) -> dict[str, list[dict[str, object]]]:
    categories = {
        "thesis": [
            claim
            for claim in claims
            if claim.agent
            in {AgentName.FINANCIALS, AgentName.EARNINGS, AgentName.INDUSTRY, AgentName.VALUATION}
            and claim.claim_type not in {"risk", "catalyst"}
        ],
        "anti_thesis": [claim for claim in claims if claim.claim_type == "risk"],
        "catalysts": [claim for claim in claims if claim.claim_type == "catalyst"],
        "watch_items": [
            claim for claim in claims if claim.status == "inferred" or _needs_corroboration(claim)
        ],
    }
    output: dict[str, list[dict[str, object]]] = {}
    for category, selected in categories.items():
        output[category] = [
            {
                "claim_id": str(claim.claim_id),
                "statement": claim.statement,
                "agent": claim.agent.value,
                "status": claim.status,
                "confidence": claim.confidence,
                "source_tier": claim.source_tier,
                "materiality": claim.materiality,
                "needs_corroboration": _needs_corroboration(claim),
                "evidence_ids": [str(item) for item in claim.evidence_ids],
            }
            for claim in sorted(selected, key=_claim_rank, reverse=True)[:8]
        ]
    return output
