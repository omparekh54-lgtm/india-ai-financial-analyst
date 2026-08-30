from app.agents.contracts import AgentName
from app.research.insights import what_changed, why_did_it_move


def test_what_changed_detects_new_and_resolved_risks() -> None:
    old_risk = {"statement": "Promoter pledge present", "claim_type": "risk"}
    new_risk = {"statement": "Recent auditor resignation", "claim_type": "risk"}
    result = what_changed(
        {
            "snapshot_at": "2026-08-20T10:00:00Z",
            "risks": [old_risk],
            "catalysts": [],
        },
        {AgentName.RISK.value: [new_risk]},
    )
    assert result["baseline_available"] is True
    assert result["new_risks"] == [new_risk]
    assert result["resolved_risks"] == [old_risk]


def test_why_move_ranks_material_company_event() -> None:
    result = why_did_it_move(
        {
            "market_metrics": {"relative_to_sector_pct": -2.0},
            "macro_metrics": {"material_macro_flags": []},
        },
        {
            AgentName.NEWS.value: [
                {
                    "statement": "Material news event detected: rating downgrade",
                    "claim_type": "risk",
                    "evidence_ids": ["e1"],
                    "data": {"materiality": "high"},
                }
            ]
        },
    )
    assert result["causality_status"] == "candidate_explanation_not_proven_causality"
    assert result["candidate_drivers"][0]["type"] == "company_event"
