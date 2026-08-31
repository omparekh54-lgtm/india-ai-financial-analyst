from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim, EvidenceRef
from app.agents.financials_agent import FinancialForensicAgent
from app.agents.validator_agent import EvidenceCrossValidationAgent
from app.orchestration.events import classify_corporate_event
from app.orchestration.plan import AnalysisMode, EventTrigger, ExecutionStage, ResearchDepth, ResearchPlan
from app.orchestration.runtime import AgentRegistry, OrchestratorRuntime


def _evidence(source_type: str = "exchange_filing") -> EvidenceRef:
    return EvidenceRef(
        source_type=source_type,
        source_uri=f"https://example.com/{uuid4()}",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="periodic",
        source_priority=1,
    )


@pytest.mark.asyncio
async def test_validator_normalizes_equivalent_crore_and_million_claims() -> None:
    first = _evidence()
    second = _evidence()
    claims = [
        Claim(
            agent=AgentName.FINANCIALS,
            statement="Revenue reported at INR 100 crore",
            claim_type="fact",
            confidence=0.9,
            evidence_ids=[first.evidence_id],
            metric="revenue",
            value=100.0,
            unit="crore",
            currency="INR",
            period="2026-06-30",
        ),
        Claim(
            agent=AgentName.EARNINGS,
            statement="Revenue reported at INR 1000 million",
            claim_type="fact",
            confidence=0.9,
            evidence_ids=[second.evidence_id],
            metric="revenue",
            value=1000.0,
            unit="million",
            currency="INR",
            period="2026-06-30",
        ),
    ]
    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="TEST",
            evidence=[first, second],
            context={"candidate_claims": [claim.model_dump(mode="json") for claim in claims]},
        )
    )

    assert result.metrics["contradiction_count"] == 0
    assert result.metrics["unit_mismatch_count"] == 0
    assert result.metrics["currency_mismatch_count"] == 0
    assert all(claim.status == "verified" for claim in result.claims)


@pytest.mark.asyncio
async def test_validator_contests_same_metric_in_different_currencies() -> None:
    first = _evidence()
    second = _evidence()
    claims = [
        Claim(
            agent=AgentName.FINANCIALS,
            statement="Revenue in INR",
            claim_type="fact",
            confidence=0.9,
            evidence_ids=[first.evidence_id],
            metric="revenue",
            value=100.0,
            unit="crore",
            currency="INR",
            period="2026-06-30",
        ),
        Claim(
            agent=AgentName.EARNINGS,
            statement="Revenue in USD",
            claim_type="fact",
            confidence=0.9,
            evidence_ids=[second.evidence_id],
            metric="revenue",
            value=100.0,
            unit="crore",
            currency="USD",
            period="2026-06-30",
        ),
    ]
    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="TEST",
            evidence=[first, second],
            context={"candidate_claims": [claim.model_dump(mode="json") for claim in claims]},
        )
    )

    assert result.metrics["currency_mismatch_count"] == 1
    assert all(claim.status == "contested" for claim in result.claims)


@pytest.mark.asyncio
async def test_financial_calculation_claim_carries_exact_fact_lineage() -> None:
    revenue_id = uuid4()
    previous_revenue_id = uuid4()
    evidence = _evidence()
    result = await FinancialForensicAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="TEST",
            evidence=[evidence],
            context={
                "security": {"sector": "Industrials", "industry": "Manufacturing"},
                "financials": {
                    "revenue": 120.0,
                    "previous_revenue": 100.0,
                    "revenue_period_end": "2026-06-30",
                    "_fact_ids": {
                        "revenue": str(revenue_id),
                        "previous_revenue": str(previous_revenue_id),
                    },
                    "_fact_units": {"revenue": "INR crore"},
                },
            },
        )
    )

    claim = next(item for item in result.claims if item.metric == "revenue_growth")
    assert claim.calculation_version == "financial.revenue_growth.v1"
    assert claim.input_metric_ids == [revenue_id, previous_revenue_id]
    assert claim.data["calculation"] == {
        "operation": "growth",
        "current": 120.0,
        "previous": 100.0,
    }


def test_event_classifier_maps_only_material_supported_events() -> None:
    assert classify_corporate_event("financial_results") == EventTrigger.QUARTERLY_RESULT
    assert classify_corporate_event("annual_report") == EventTrigger.ANNUAL_REPORT
    assert classify_corporate_event("auditor_resignation") == EventTrigger.GOVERNANCE_FILING
    assert classify_corporate_event("routine_board_meeting") is None


class _RepairingFinancialAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        self.calls += 1
        return AgentOutput(
            agent=AgentName.FINANCIALS,
            claims=[
                Claim(
                    agent=AgentName.FINANCIALS,
                    statement=f"Financial calculation pass {self.calls}",
                    claim_type="calculation",
                    confidence=0.9,
                    evidence_ids=[],
                    metric="test_metric",
                    value=float(self.calls),
                )
            ],
        )


class _RepairValidator:
    def __init__(self) -> None:
        self.calls = 0

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        self.calls += 1
        status = "contested" if self.calls == 1 else "supported"
        candidate = (agent_input.context.get("candidate_claims") or [])[0]
        claim = Claim.model_validate(candidate).model_copy(update={"status": status})
        tasks = (
            [
                {
                    "claim_id": str(claim.claim_id),
                    "agent": AgentName.FINANCIALS.value,
                    "reason": "recomputation mismatch",
                    "required_action": "rerun originating agent",
                    "retryable": True,
                }
            ]
            if self.calls == 1
            else []
        )
        return AgentOutput(
            agent=AgentName.VALIDATOR,
            claims=[claim],
            metrics={"repair_tasks": tasks},
        )


class _SynthesisAgent:
    def __init__(self) -> None:
        self.calls = 0
        self.validated_statuses: list[str] = []

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        self.calls += 1
        validated = agent_input.context.get("validated_claims") or []
        self.validated_statuses = [str(item.get("status")) for item in validated if isinstance(item, dict)]
        return AgentOutput(agent=AgentName.SYNTHESIS, metrics={"report": {"claim_count": len(validated)}})


@pytest.mark.asyncio
async def test_orchestrator_runs_one_bounded_repair_before_synthesis() -> None:
    financial = _RepairingFinancialAgent()
    validator = _RepairValidator()
    synthesis = _SynthesisAgent()
    registry = AgentRegistry(
        {
            AgentName.FINANCIALS: financial,
            AgentName.VALIDATOR: validator,
            AgentName.SYNTHESIS: synthesis,
        }
    )
    runtime = OrchestratorRuntime(registry)
    plan = ResearchPlan(
        mode=AnalysisMode.FUNDAMENTALS,
        depth=ResearchDepth.STANDARD,
        stages=[
            ExecutionStage(name="collect", agents=[AgentName.FINANCIALS], parallel=False),
            ExecutionStage(name="validate", agents=[AgentName.VALIDATOR], parallel=False),
            ExecutionStage(name="synthesize", agents=[AgentName.SYNTHESIS], parallel=False),
        ],
    )
    progress: list[tuple[str, int]] = []

    async def on_stage(stage: str, value: int) -> None:
        progress.append((stage, value))

    outputs = await runtime.run(
        plan,
        AgentInput(job_id=uuid4(), query="TEST"),
        on_stage=on_stage,
    )

    assert financial.calls == 2
    assert validator.calls == 2
    assert synthesis.calls == 1
    assert synthesis.validated_statuses == ["supported"]
    assert ("repairing", 82) in progress
    assert outputs[-1].agent == AgentName.SYNTHESIS
