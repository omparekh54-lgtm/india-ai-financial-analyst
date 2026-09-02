from __future__ import annotations

from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim, EvidenceRef
from app.agents.llm_enrichment import LlmEnrichedAgent, LlmSynthesisAgent
from app.providers.client import ChatResult, ProviderCallError
from app.providers.router import Capability


class BaseAgent:
    async def run(self, agent_input: AgentInput) -> AgentOutput:
        return AgentOutput(
            agent=AgentName.NEWS,
            claims=[
                Claim(
                    agent=AgentName.NEWS,
                    statement="Deterministic event detected",
                    claim_type="catalyst",
                    confidence=0.8,
                    evidence_ids=[agent_input.evidence[0].evidence_id],
                    status="pending",
                )
            ],
            evidence=agent_input.evidence,
            metrics={"event_count": 1},
        )


class SynthesisBaseAgent:
    async def run(self, agent_input: AgentInput) -> AgentOutput:
        return AgentOutput(
            agent=AgentName.SYNTHESIS,
            metrics={
                "report": {
                    "query": agent_input.query,
                    "confidence": {"data_confidence": 0.9},
                }
            },
        )


class FakeGateway:
    def __init__(self, content: str, *, enabled: bool = True, fail: bool = False) -> None:
        self.enabled = enabled
        self.content = content
        self.fail = fail
        self.budget_keys: list[str | None] = []

    async def complete(
        self,
        capability: Capability,
        messages: list[object],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        budget_key: str | None = None,
    ) -> ChatResult:
        del capability, messages, temperature, max_tokens
        self.budget_keys.append(budget_key)
        if self.fail:
            raise ProviderCallError("test failure")
        return ChatResult(
            provider="test",
            model="test-model",
            content=self.content,
            input_tokens=10,
            output_tokens=5,
        )

    def job_usage(self, budget_key: str) -> dict[str, object]:
        return {
            "calls": sum(key == budget_key for key in self.budget_keys),
            "reserved_tokens": 100,
            "actual_tokens": 15,
            "provider_attempts": {"test": 1},
            "max_calls": 10,
            "max_reserved_tokens": 24000,
        }


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://example.com/filing.pdf",
        title="Quarterly filing",
        retrieved_at="2026-08-30T15:00:00+00:00",
        freshness="near_live",
        excerpt="Management reported improving demand but highlighted input-cost pressure.",
        page_number=2,
        source_priority=1,
    )


@pytest.mark.asyncio
async def test_llm_enrichment_adds_only_evidence_linked_pending_claims() -> None:
    evidence = _evidence()
    gateway = FakeGateway(
        '{"insights":[{"statement":"Input-cost pressure may constrain margins",'
        '"claim_type":"risk","confidence":0.9,"evidence_keys":["E1"]}]}'
    )
    agent = LlmEnrichedAgent(
        BaseAgent(),
        agent=AgentName.NEWS,
        gateway=gateway,  # type: ignore[arg-type]
        capability=Capability.FAST_REASONING,
    )
    job_id = uuid4()

    result = await agent.run(AgentInput(job_id=job_id, query="EXAMPLE", evidence=[evidence]))

    assert len(result.claims) == 2
    enriched = result.claims[-1]
    assert enriched.status == "pending"
    assert enriched.claim_type == "risk"
    assert enriched.evidence_ids == [evidence.evidence_id]
    assert enriched.confidence == 0.78
    assert result.metrics["llm_enrichment"]["provider"] == "test"
    assert gateway.budget_keys == [str(job_id)]


@pytest.mark.asyncio
async def test_llm_enrichment_failure_preserves_deterministic_output() -> None:
    evidence = _evidence()
    agent = LlmEnrichedAgent(
        BaseAgent(),
        agent=AgentName.NEWS,
        gateway=FakeGateway("{}", fail=True),  # type: ignore[arg-type]
        capability=Capability.FAST_REASONING,
    )

    result = await agent.run(
        AgentInput(job_id=uuid4(), query="EXAMPLE", evidence=[evidence])
    )

    assert len(result.claims) == 1
    assert result.claims[0].statement == "Deterministic event detected"
    assert result.warnings


@pytest.mark.asyncio
async def test_llm_disabled_is_zero_behavior_change() -> None:
    evidence = _evidence()
    agent = LlmEnrichedAgent(
        BaseAgent(),
        agent=AgentName.NEWS,
        gateway=FakeGateway("{}", enabled=False),  # type: ignore[arg-type]
        capability=Capability.FAST_REASONING,
    )

    result = await agent.run(
        AgentInput(job_id=uuid4(), query="EXAMPLE", evidence=[evidence])
    )

    assert len(result.claims) == 1
    assert "llm_enrichment" not in result.metrics


@pytest.mark.asyncio
async def test_llm_synthesis_adds_prose_without_new_claims() -> None:
    evidence = _evidence()
    validated = Claim(
        agent=AgentName.FINANCIALS,
        statement="Revenue growth was positive in the latest reported period",
        claim_type="fact",
        confidence=0.9,
        evidence_ids=[evidence.evidence_id],
        status="verified",
    )
    gateway = FakeGateway(
        '{"executive_summary":"Validated evidence indicates positive reported revenue growth.",'
        '"bull_case":["Positive reported growth"],"bear_case":[],"watch_items":[],'
        '"confidence_note":"Evidence is limited to validated reported claims."}'
    )
    agent = LlmSynthesisAgent(
        SynthesisBaseAgent(),
        gateway,  # type: ignore[arg-type]
    )

    result = await agent.run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            evidence=[evidence],
            context={"validated_claims": [validated.model_dump(mode="json")]},
        )
    )

    report = result.metrics["report"]
    assert report["executive_summary"].startswith("Validated evidence")
    assert result.claims == []
    assert result.metrics["llm_synthesis"]["provider"] == "test"
    assert result.metrics["llm_synthesis"]["job_budget"]["calls"] == 1
