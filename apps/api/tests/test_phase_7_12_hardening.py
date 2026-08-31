from __future__ import annotations

from types import MethodType
from uuid import UUID, uuid4

import pytest

from app.agents.contracts import AgentInput, AgentName, Claim, EvidenceRef
from app.agents.synthesis_agent import ChiefAnalystAgent
from app.agents.validator_agent import EvidenceCrossValidationAgent
from app.core.config import Settings
from app.orchestration.plan import AnalysisMode, ResearchDepth
from app.providers.client import ChatMessage, ProviderCallError
from app.providers.gateway import ProviderGateway
from app.research.event_dispatch import EventResearchDispatcher
from app.research.insights import what_changed, why_did_it_move


@pytest.mark.asyncio
async def test_provider_gateway_enforces_shared_per_job_call_budget() -> None:
    settings = Settings(
        llm_max_calls_per_job=1,
        llm_max_reserved_tokens_per_job=10000,
    )
    gateway = ProviderGateway(settings)
    await gateway._reserve_job_budget(  # noqa: SLF001 - explicit unit test of hard budget boundary
        "job-1",
        provider="groq",
        messages=[ChatMessage(role="user", content="test")],
        max_tokens=100,
    )

    with pytest.raises(ProviderCallError, match="call budget exhausted"):
        await gateway._reserve_job_budget(  # noqa: SLF001
            "job-1",
            provider="gemini",
            messages=[ChatMessage(role="user", content="fallback")],
            max_tokens=100,
        )

    usage = gateway.job_usage("job-1")
    assert usage["calls"] == 1
    assert usage["provider_attempts"] == {"groq": 1}


@pytest.mark.asyncio
async def test_validator_flags_uncorroborated_high_impact_secondary_claim() -> None:
    evidence = EvidenceRef(
        source_type="news",
        source_uri="https://example.com/story",
        retrieved_at="2026-08-31T12:00:00+00:00",
        freshness="near_live",
        source_priority=3,
    )
    claim = Claim(
        agent=AgentName.NEWS,
        statement="A high-impact event may affect the company",
        claim_type="risk",
        confidence=0.90,
        evidence_ids=[evidence.evidence_id],
        status="pending",
        materiality=0.90,
    )

    result = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="TEST",
            evidence=[evidence],
            context={"candidate_claims": [claim.model_dump(mode="json")]},
        )
    )

    validated = result.claims[0]
    assert validated.status == "supported"
    assert validated.confidence == 0.55
    assert validated.data["validator_needs_corroboration"] is True
    assert result.metrics["uncorroborated_high_impact_count"] == 1
    assert result.metrics["repair_tasks"][0]["retryable"] is False


@pytest.mark.asyncio
async def test_chief_analyst_maps_evidence_and_keeps_uncorroborated_catalyst_out_of_core_thesis() -> None:
    strong = Claim(
        agent=AgentName.FINANCIALS,
        statement="Cash conversion improved",
        claim_type="fact",
        confidence=0.92,
        status="verified",
        source_tier="A",
        materiality=0.85,
    )
    uncorroborated = Claim(
        agent=AgentName.NEWS,
        statement="A media report suggests a potential large order",
        claim_type="catalyst",
        confidence=0.55,
        status="supported",
        source_tier="C",
        materiality=0.90,
        data={"validator_needs_corroboration": True},
    )

    result = await ChiefAnalystAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="TEST",
            context={
                "validated_claims": [
                    strong.model_dump(mode="json"),
                    uncorroborated.model_dump(mode="json"),
                ]
            },
        )
    )

    report = result.metrics["report"]
    assert report["investment_thesis"] == ["Cash conversion improved"]
    assert report["catalysts"] == []
    assert report["thesis_evidence_map"]["watch_items"][0]["needs_corroboration"] is True
    assert report["confidence"]["data_confidence"] > report["confidence"]["catalyst_confidence"]


def test_what_changed_uses_stable_event_identity_when_wording_changes() -> None:
    previous = {
        "statement": "Auditor submitted its resignation",
        "claim_type": "risk",
        "agent": AgentName.RISK.value,
        "data": {
            "event_type": "auditor_resignation",
            "source_uri": "https://example.com/filing-1",
        },
    }
    current = {
        "statement": "Recent auditor resignation requires review",
        "claim_type": "risk",
        "agent": AgentName.RISK.value,
        "data": {
            "event_type": "auditor_resignation",
            "source_uri": "https://example.com/filing-1",
        },
    }

    result = what_changed(
        {
            "snapshot_at": "2026-08-30T10:00:00Z",
            "risks": [previous],
            "catalysts": [],
        },
        {AgentName.RISK.value: [current]},
    )

    assert result["new_risks"] == []
    assert result["resolved_risks"] == []


def test_why_move_separates_sector_and_volume_context() -> None:
    result = why_did_it_move(
        {
            "market_metrics": {
                "change_pct": 3.0,
                "benchmark_change_pct": 1.2,
                "sector_change_pct": 2.4,
                "relative_to_benchmark_pct": 1.8,
                "relative_to_sector_pct": 0.6,
                "volume_ratio": 2.2,
            },
            "macro_metrics": {"material_macro_flags": []},
        },
        {},
    )

    types = {driver["type"] for driver in result["candidate_drivers"]}
    assert "absolute_stock_move" in types
    assert "broad_market_factor" in types
    assert "sector_factor" in types
    assert "volume_confirmation" in types
    assert result["volume_ratio"] == 2.2
    assert result["causality_status"] == "candidate_explanation_not_proven_causality"


class _FakeWatchlists:
    def __init__(self, subscribers: list[UUID]) -> None:
        self.subscribers = subscribers

    async def event_subscribers(self, security_id: UUID) -> list[UUID]:
        del security_id
        return self.subscribers


class _FakeResearchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def enqueue(
        self,
        *,
        query: str,
        mode: AnalysisMode,
        depth: ResearchDepth,
        requested_by: UUID | None,
        metadata: dict[str, object] | None = None,
    ) -> UUID:
        self.calls.append(
            {
                "query": query,
                "mode": mode,
                "depth": depth,
                "requested_by": requested_by,
                "metadata": metadata or {},
            }
        )
        return uuid4()


@pytest.mark.asyncio
async def test_event_dispatch_creates_one_private_job_per_watchlist_subscriber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_gate(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr("app.research.event_dispatch.enforce_research_corpus_ready", fake_gate)
    users = [uuid4(), uuid4()]
    dispatcher = EventResearchDispatcher.__new__(EventResearchDispatcher)
    dispatcher.enabled = True
    dispatcher.engine = object()  # type: ignore[assignment]
    dispatcher.settings = Settings()
    dispatcher.watchlists = _FakeWatchlists(users)  # type: ignore[assignment]
    service = _FakeResearchService()
    dispatcher.service = service  # type: ignore[assignment]

    async def never_duplicate(self: EventResearchDispatcher, event_id: UUID, user_id: UUID) -> bool:
        del self, event_id, user_id
        return False

    dispatcher._already_enqueued = MethodType(never_duplicate, dispatcher)  # type: ignore[method-assign]
    result = await dispatcher.dispatch_corporate_event(
        event_id=uuid4(),
        security_id=uuid4(),
        event_type="financial_results",
        query="RELIANCE",
        headline="Quarterly results",
        published_at="2026-08-31T12:00:00+00:00",
    )

    assert result["queued_count"] == 2
    assert {call["requested_by"] for call in service.calls} == set(users)
    assert all(call["mode"] == AnalysisMode.WHAT_CHANGED for call in service.calls)
    assert all(call["metadata"]["watchlist_trigger"] is True for call in service.calls)
