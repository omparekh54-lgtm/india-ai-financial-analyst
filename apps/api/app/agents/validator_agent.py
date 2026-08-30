from __future__ import annotations

from uuid import UUID

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim, EvidenceRef


class EvidenceCrossValidationAgent:
    """Enforces evidence coverage and semantic claim status before synthesis."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        raw_claims = agent_input.context.get("candidate_claims") or []
        claims = [claim if isinstance(claim, Claim) else Claim.model_validate(claim) for claim in raw_claims]
        evidence_by_id = {item.evidence_id: item for item in agent_input.evidence}

        validated = [self._validate_claim(claim, evidence_by_id) for claim in claims]
        unsupported = sum(claim.status == "unsupported" for claim in validated)
        stale = sum(claim.status == "stale" for claim in validated)
        contested = sum(claim.status == "contested" for claim in validated)

        return AgentOutput(
            agent=AgentName.VALIDATOR,
            ok=unsupported == 0 and contested == 0,
            claims=validated,
            evidence=agent_input.evidence,
            metrics={
                "claim_count": len(validated),
                "unsupported_count": unsupported,
                "stale_count": stale,
                "contested_count": contested,
                "evidence_coverage": _evidence_coverage(validated),
            },
            warnings=[
                warning
                for warning in (
                    f"{unsupported} claims lack evidence" if unsupported else None,
                    f"{stale} claims depend on stale evidence" if stale else None,
                )
                if warning is not None
            ],
        )

    def _validate_claim(
        self,
        claim: Claim,
        evidence_by_id: dict[UUID, EvidenceRef],
    ) -> Claim:
        linked = [evidence_by_id[item] for item in claim.evidence_ids if item in evidence_by_id]

        if not linked:
            if claim.claim_type == "scenario":
                return claim.model_copy(
                    update={"status": "inferred", "confidence": min(claim.confidence, 0.60)}
                )
            return claim.model_copy(
                update={"status": "unsupported", "confidence": min(claim.confidence, 0.35)}
            )

        if any(item.freshness == "historical" for item in linked) and claim.data.get(
            "requires_current_data"
        ):
            return claim.model_copy(
                update={"status": "stale", "confidence": min(claim.confidence, 0.5)}
            )

        primary_types = {
            "exchange_filing",
            "company_filing",
            "regulator",
            "official_macro",
            "official_flow",
            "market_data",
        }
        has_primary = any(item.source_type in primary_types for item in linked)
        ai_only = all(item.source_type == "ai_extraction" for item in linked)
        if ai_only:
            if claim.claim_type in {"inference", "scenario"}:
                return claim.model_copy(
                    update={"status": "inferred", "confidence": min(claim.confidence, 0.65)}
                )
            return claim.model_copy(
                update={"status": "supported", "confidence": min(claim.confidence, 0.60)}
            )

        confidence_floor = 0.8 if has_primary else 0.6
        confidence = max(claim.confidence, confidence_floor)

        if claim.claim_type in {"inference", "scenario"}:
            return claim.model_copy(update={"status": "inferred", "confidence": confidence})
        if claim.claim_type in {"risk", "catalyst"}:
            return claim.model_copy(update={"status": "supported", "confidence": confidence})

        status = "verified" if has_primary else "supported"
        return claim.model_copy(update={"status": status, "confidence": confidence})


def _evidence_coverage(claims: list[Claim]) -> float:
    if not claims:
        return 1.0
    supported = sum(bool(claim.evidence_ids) or claim.claim_type == "scenario" for claim in claims)
    return supported / len(claims)
