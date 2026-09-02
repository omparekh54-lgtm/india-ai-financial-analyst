from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, EvidenceRef
from app.agents.filings_agent import FilingsGovernanceAgent
from app.ingestion.exchange import classify_exchange_event, should_follow_exchange_document
from app.research.live_context import _build_earnings_context


def _evidence(*, section: str, excerpt: str, page: int = 1) -> EvidenceRef:
    now = datetime.now(UTC).isoformat()
    return EvidenceRef(
        source_type="exchange_filing",
        source_uri="https://nsearchives.nseindia.com/example.pdf",
        title="Official NSE filing",
        published_at=now,
        retrieved_at=now,
        freshness="near_live",
        excerpt=excerpt,
        page_number=page,
        section=section,
        source_priority=1,
    )


def test_exchange_classifier_covers_india_governance_events() -> None:
    assert classify_exchange_event("Change in CFO - resignation")[0] == "cfo_change"
    assert classify_exchange_event("SEBI order and penalty disclosure")[0] == "regulatory_action"
    assert classify_exchange_event("Regulation 34 - Annual Report FY 2026")[0] == "annual_report"
    assert classify_exchange_event("Credit rating downgrade by ICRA")[0] == "credit_rating"
    assert should_follow_exchange_document("auditor_resignation") is True
    assert should_follow_exchange_document("shareholding_pattern") is False


def test_earnings_context_uses_normalized_facts_and_transcript_chunks() -> None:
    evidence = [
        _evidence(
            section="earnings_transcript",
            excerpt="Management noted robust demand and a strong pipeline.",
            page=4,
        ),
        _evidence(
            section="financial_results",
            excerpt="Quarterly financial results for the period ended June 2026.",
            page=1,
        ),
    ]
    result = _build_earnings_context(
        {
            "revenue": 1250.0,
            "previous_revenue": 1000.0,
            "pat": 180.0,
            "previous_pat": 150.0,
            "ebitda": 260.0,
            "revenue_period_end": "2026-06-30",
        },
        evidence,
    )

    assert result["revenue"] == 1250.0
    assert result["prior_revenue"] == 1000.0
    assert result["period"] == "2026-06-30"
    assert "robust demand" in str(result["management_commentary"])
    assert result["published_at"] == evidence[1].published_at


@pytest.mark.asyncio
async def test_filings_agent_uses_normalized_section_without_keyword_repetition() -> None:
    evidence = _evidence(
        section="auditor_resignation",
        excerpt="The statutory firm communicated its decision effective immediately.",
        page=3,
    )
    result = await FilingsGovernanceAgent().run(
        AgentInput(job_id=uuid4(), query="EXAMPLE", evidence=[evidence])
    )

    assert result.metrics["detected_event_count"] == 1
    assert result.claims[0].data["event_type"] == "auditor_resignation"
    assert result.claims[0].data["page_number"] == 3
    assert result.claims[0].evidence_ids == [evidence.evidence_id]
