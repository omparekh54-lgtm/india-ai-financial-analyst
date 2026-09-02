from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import fitz
import httpx
import pytest

from app.agents.contracts import AgentInput, AgentName, Claim, EvidenceRef
from app.agents.validator_agent import EvidenceCrossValidationAgent
from app.core.config import Settings
from app.documents.visual import GeminiDocumentVisualAnalyzer, render_visual_pages


def _visual_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Quarterly performance overview")
    for index in range(12):
        y = 140 + index * 12
        page.draw_line((80, y), (200 + index * 8, y))
    data = document.tobytes()
    document.close()
    return data


def test_render_visual_pages_selects_chart_like_page() -> None:
    pages = render_visual_pages(
        _visual_pdf(),
        page_text=["Quarterly performance overview"],
        max_pages=2,
    )
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].png_bytes.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_gemini_visual_analysis_is_mocked_bounded_and_page_aware() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "findings": [
                                                {
                                                    "page_number": 1,
                                                    "kind": "chart",
                                                    "summary": "The page contains a chart-like quarterly performance visual.",
                                                    "confidence": 0.95,
                                                },
                                                {
                                                    "page_number": 99,
                                                    "kind": "table",
                                                    "summary": "This page was not supplied and must be dropped.",
                                                    "confidence": 0.8,
                                                },
                                            ]
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    settings = Settings(
        enable_external_llm_calls=True,
        enable_multimodal_document_analysis=True,
        gemini_api_key="test-key",
        gemini_model="gemini-test",
        multimodal_max_pages_per_document=2,
    )
    analyzer = GeminiDocumentVisualAnalyzer(
        settings,
        transport=httpx.MockTransport(handler),
    )
    result = await analyzer.analyze_pdf(
        _visual_pdf(),
        title="Quarterly filing",
        page_text=["Quarterly performance overview"],
    )

    assert result is not None
    assert result.analyzed_pages == [1]
    assert len(result.findings) == 1
    assert result.findings[0].page_number == 1
    assert result.findings[0].confidence == 0.80
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["generationConfig"]["responseMimeType"] == "application/json"  # type: ignore[index]


@pytest.mark.asyncio
async def test_ai_visual_evidence_cannot_be_verified_primary_fact() -> None:
    evidence = EvidenceRef(
        source_type="ai_extraction",
        source_uri="https://www.nseindia.com/example.pdf",
        title="Official filing",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
        excerpt="AI-assisted visual interpretation of page 4.",
        page_number=4,
        section="multimodal_extraction",
        source_priority=4,
    )
    claim = Claim(
        agent=AgentName.RISK,
        statement="The visual suggests a possible margin-pressure trend.",
        claim_type="inference",
        confidence=0.9,
        evidence_ids=[evidence.evidence_id],
        status="pending",
    )
    output = await EvidenceCrossValidationAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            context={"candidate_claims": [claim]},
            evidence=[evidence],
        )
    )

    assert output.claims[0].status == "inferred"
    assert output.claims[0].confidence == 0.65
