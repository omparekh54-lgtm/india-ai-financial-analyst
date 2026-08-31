from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from uuid import UUID

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim, EvidenceRef


@dataclass(frozen=True)
class RecomputeResult:
    attempted: bool
    matches: bool
    value: float | None = None


class EvidenceCrossValidationAgent:
    """Recompute, source-grade, reconcile contradictions, and emit targeted repair tasks."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        raw_claims = agent_input.context.get("candidate_claims") or []
        claims = [claim if isinstance(claim, Claim) else Claim.model_validate(claim) for claim in raw_claims]
        evidence_by_id = {item.evidence_id: item for item in agent_input.evidence}

        initial = [self._validate_claim(claim, evidence_by_id) for claim in claims]
        validated, contradiction_count = self._reconcile_conflicts(initial, evidence_by_id)

        unsupported = sum(claim.status == "unsupported" for claim in validated)
        stale = sum(claim.status == "stale" for claim in validated)
        contested = sum(claim.status == "contested" for claim in validated)
        recomputed = sum(
            bool(claim.data.get("validator_recomputed"))
            for claim in validated
            if isinstance(claim.data, dict)
        )
        repair_tasks = _repair_tasks(validated)

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
                "contradiction_count": contradiction_count,
                "recomputed_count": recomputed,
                "evidence_coverage": _evidence_coverage(validated),
                "repair_tasks": repair_tasks,
            },
            warnings=[
                warning
                for warning in (
                    f"{unsupported} claims lack evidence" if unsupported else None,
                    f"{stale} claims depend on stale evidence" if stale else None,
                    f"{contested} claims are contested" if contested else None,
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

        best_evidence = min(linked, key=lambda item: item.source_priority)
        audit_updates: dict[str, object] = {
            "source_tier": claim.source_tier or best_evidence.source_tier,
            "freshness_at": claim.freshness_at or best_evidence.published_at or best_evidence.retrieved_at,
        }

        if any(item.freshness == "historical" for item in linked) and claim.data.get(
            "requires_current_data"
        ):
            audit_updates.update({"status": "stale", "confidence": min(claim.confidence, 0.5)})
            return claim.model_copy(update=audit_updates)

        recompute = _recompute_claim(claim)
        if recompute.attempted:
            data = dict(claim.data)
            data["validator_recomputed"] = True
            data["validator_recomputed_value"] = recompute.value
            audit_updates["data"] = data
            if not recompute.matches:
                audit_updates.update(
                    {
                        "status": "contested",
                        "confidence": min(claim.confidence, 0.45),
                    }
                )
                return claim.model_copy(update=audit_updates)

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
                audit_updates.update(
                    {"status": "inferred", "confidence": min(claim.confidence, 0.65)}
                )
            else:
                audit_updates.update(
                    {"status": "supported", "confidence": min(claim.confidence, 0.60)}
                )
            return claim.model_copy(update=audit_updates)

        confidence_floor = 0.8 if has_primary else 0.6
        confidence = max(claim.confidence, confidence_floor)

        if claim.claim_type in {"inference", "scenario"}:
            audit_updates.update({"status": "inferred", "confidence": confidence})
            return claim.model_copy(update=audit_updates)
        if claim.claim_type in {"risk", "catalyst"}:
            audit_updates.update({"status": "supported", "confidence": confidence})
            return claim.model_copy(update=audit_updates)

        audit_updates.update(
            {
                "status": "verified" if has_primary else "supported",
                "confidence": confidence,
            }
        )
        return claim.model_copy(update=audit_updates)

    def _reconcile_conflicts(
        self,
        claims: list[Claim],
        evidence_by_id: dict[UUID, EvidenceRef],
    ) -> tuple[list[Claim], int]:
        groups: dict[tuple[str, str], list[int]] = {}
        for index, claim in enumerate(claims):
            metric = _claim_metric(claim)
            value = _claim_value(claim)
            if metric is None or value is None or claim.status in {"unsupported", "stale"}:
                continue
            period = claim.period or str(claim.data.get("period") or "unspecified")
            groups.setdefault((metric.lower().strip(), period), []).append(index)

        reconciled = list(claims)
        contradiction_count = 0
        for indices in groups.values():
            if len(indices) < 2:
                continue
            values = [_claim_value(reconciled[index]) for index in indices]
            numeric_values = [value for value in values if value is not None]
            if len(numeric_values) < 2 or _values_agree(numeric_values):
                continue

            contradiction_count += 1
            ranked = sorted(
                indices,
                key=lambda index: _claim_source_priority(reconciled[index], evidence_by_id),
            )
            best_priority = _claim_source_priority(reconciled[ranked[0]], evidence_by_id)
            best = [
                index
                for index in ranked
                if _claim_source_priority(reconciled[index], evidence_by_id) == best_priority
            ]

            if len(best) == 1:
                winner = best[0]
                winner_data = dict(reconciled[winner].data)
                winner_data["validator_conflict_resolved_by_source_priority"] = True
                reconciled[winner] = reconciled[winner].model_copy(update={"data": winner_data})
                losing = [index for index in indices if index != winner]
            else:
                losing = indices

            for index in losing:
                claim = reconciled[index]
                data = dict(claim.data)
                data["validator_conflict"] = True
                reconciled[index] = claim.model_copy(
                    update={
                        "status": "contested",
                        "confidence": min(claim.confidence, 0.50),
                        "data": data,
                    }
                )

        return reconciled, contradiction_count


def _claim_metric(claim: Claim) -> str | None:
    if claim.metric:
        return claim.metric
    raw = claim.data.get("metric")
    return str(raw) if raw is not None else None


def _claim_value(claim: Claim) -> float | None:
    if claim.value is not None:
        return claim.value
    return _number(claim.data.get("value"))


def _claim_source_priority(claim: Claim, evidence_by_id: dict[UUID, EvidenceRef]) -> int:
    linked = [evidence_by_id[item] for item in claim.evidence_ids if item in evidence_by_id]
    if not linked:
        return 99
    return min(item.source_priority for item in linked)


def _values_agree(values: list[float]) -> bool:
    anchor = values[0]
    return all(isclose(anchor, value, rel_tol=1e-6, abs_tol=1e-9) for value in values[1:])


def _recompute_claim(claim: Claim) -> RecomputeResult:
    if claim.claim_type != "calculation":
        return RecomputeResult(attempted=False, matches=True)
    calculation = claim.data.get("calculation")
    if not isinstance(calculation, dict):
        return RecomputeResult(attempted=False, matches=True)

    operation = str(calculation.get("operation") or "").strip().lower()
    expected = _claim_value(claim)
    if expected is None:
        return RecomputeResult(attempted=False, matches=True)

    result: float | None = None
    if operation == "ratio":
        numerator = _number(calculation.get("numerator"))
        denominator = _number(calculation.get("denominator"))
        scale = _number(calculation.get("scale"))
        if numerator is not None and denominator not in {None, 0.0}:
            assert denominator is not None
            result = numerator / denominator * (scale if scale is not None else 1.0)
    elif operation == "growth":
        current = _number(calculation.get("current"))
        previous = _number(calculation.get("previous"))
        if current is not None and previous not in {None, 0.0}:
            assert previous is not None
            result = current / previous - 1.0
    elif operation == "difference":
        left = _number(calculation.get("left"))
        right = _number(calculation.get("right"))
        if left is not None and right is not None:
            result = left - right
    elif operation == "sum":
        raw_values = calculation.get("values")
        if isinstance(raw_values, list):
            parsed = [_number(value) for value in raw_values]
            if all(value is not None for value in parsed):
                result = sum(value for value in parsed if value is not None)

    if result is None:
        return RecomputeResult(attempted=False, matches=True)
    return RecomputeResult(
        attempted=True,
        matches=isclose(expected, result, rel_tol=1e-6, abs_tol=1e-9),
        value=result,
    )


def _repair_tasks(claims: list[Claim]) -> list[dict[str, str]]:
    tasks: list[dict[str, str]] = []
    for claim in claims:
        action: str | None = None
        reason: str | None = None
        if claim.status == "unsupported":
            action = "fetch stronger evidence and rerun originating agent"
            reason = "missing evidence"
        elif claim.status == "contested":
            action = "reconcile source period/unit/calculation inputs and rerun originating agent"
            reason = "contradiction or recomputation mismatch"
        elif claim.status == "stale":
            action = "refresh required current evidence and rerun originating agent"
            reason = "stale evidence"
        if action and reason:
            tasks.append(
                {
                    "claim_id": str(claim.claim_id),
                    "agent": claim.agent.value,
                    "reason": reason,
                    "required_action": action,
                }
            )
    return tasks


def _evidence_coverage(claims: list[Claim]) -> float:
    if not claims:
        return 1.0
    supported = sum(bool(claim.evidence_ids) or claim.claim_type == "scenario" for claim in claims)
    return supported / len(claims)


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
