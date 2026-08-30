from __future__ import annotations

from uuid import UUID

import pytest

from app.research.export import render_research_markdown, research_export_payload

JOB_ID = UUID("11111111-1111-4111-8111-111111111111")


def _job() -> dict[str, object]:
    return {
        "id": JOB_ID,
        "query": "TESTCO",
        "mode": "full_analysis",
        "status": "completed",
        "security_id": UUID("22222222-2222-4222-8222-222222222222"),
        "legal_name": "Test Company Limited",
        "nse_symbol": "TESTCO",
        "report_json": {
            "query": "TESTCO",
            "mode": "full_analysis",
            "claim_count": 1,
            "security": {
                "legal_name": "Test Company Limited",
                "nse_symbol": "TESTCO",
            },
            "executive_summary": "Persisted executive summary.",
            "narrative": {
                "bull_case": ["Persisted upside consideration."],
                "bear_case": ["Persisted downside consideration."],
                "watch_items": ["Persisted watch item."],
                "confidence_note": "Persisted confidence note.",
            },
            "confidence": {"data_confidence": 0.8},
            "sections": {
                "filings": [
                    {
                        "claim_id": "claim-1",
                        "statement": "Persisted source-linked claim.",
                        "status": "verified",
                        "confidence": 0.75,
                        "evidence_ids": ["evidence-1"],
                    }
                ]
            },
            "evidence_catalog": {
                "evidence-1": {
                    "source_type": "exchange_filing",
                    "source_uri": "https://source.test/filing",
                    "title": "Persisted filing evidence",
                    "published_at": "2026-08-30T10:00:00+00:00",
                }
            },
            "warnings": ["Persisted warning."],
            "validation": {"evidence_coverage": 1.0},
            "research_disclaimer": "Persisted disclaimer.",
        },
        "data_confidence": 0.8,
        "thesis_confidence": 0.7,
        "valuation_confidence": 0.6,
        "catalyst_confidence": 0.5,
    }


def test_markdown_export_uses_persisted_report_and_evidence() -> None:
    markdown = render_research_markdown(_job())

    assert "# Test Company Limited" in markdown
    assert "Persisted executive summary." in markdown
    assert "Persisted source-linked claim." in markdown
    assert "https://source.test/filing" in markdown
    assert "Evidence coverage: 100%" in markdown
    assert "Persisted disclaimer." in markdown


def test_export_payload_does_not_include_auth_identity() -> None:
    job = _job()
    job["requested_by"] = "private-user-id"
    job["email"] = "private@example.test"

    payload = research_export_payload(job)

    assert "requested_by" not in payload
    assert "email" not in payload
    assert payload["job_id"] == str(JOB_ID)
    assert payload["report"] == job["report_json"]


def test_markdown_export_rejects_missing_persisted_report() -> None:
    with pytest.raises(ValueError, match="persisted report"):
        render_research_markdown({"id": JOB_ID, "query": "TESTCO", "report_json": None})
