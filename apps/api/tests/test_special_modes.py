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


def test_what_changed_compares_disclosures_metrics_valuation_and_confidence() -> None:
    old_disclosure = {
        "statement": "Previous quarterly result was filed",
        "claim_type": "fact",
    }
    new_disclosure = {
        "statement": "New quarterly result shows margin expansion",
        "claim_type": "fact",
    }
    result = what_changed(
        {
            "snapshot_at": "2026-08-20T10:00:00Z",
            "risks": [],
            "catalysts": [],
            "metrics": {
                "market": {"return_1d_pct": 1.0},
                "financials": {"revenue_growth": 0.10},
                "valuation": {"base_case_value": 100.0},
                "confidence": {"data_confidence": 0.80},
            },
            "metadata": {"disclosure_claims": [old_disclosure]},
        },
        {
            AgentName.FILINGS.value: [new_disclosure],
            AgentName.RISK.value: [],
        },
        context={
            "market_metrics": {"return_1d_pct": 2.5},
            "financial_metrics": {"revenue_growth": 0.15},
            "valuation_metrics": {"base_case_value": 112.0},
        },
        current_confidence={"data_confidence": 0.90},
    )

    assert result["new_disclosures"] == [new_disclosure]
    assert result["market_changes"][0]["metric"] == "return_1d_pct"
    assert result["financial_changes"][0]["current"] == 0.15
    assert result["valuation_changes"][0]["absolute_change"] == 12.0
    assert result["confidence_changes"][0]["current"] == 0.90


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


def test_why_move_uses_technical_and_derivatives_as_context_not_proven_cause() -> None:
    result = why_did_it_move(
        {
            "market_metrics": {"relative_to_benchmark_pct": 1.0},
            "macro_metrics": {"material_macro_flags": []},
            "technical_metrics": {
                "rsi_14": 74.0,
                "realized_volatility_20d": 0.52,
                "derivatives": {
                    "futures_basis_pct": 1.2,
                    "futures_oi_change_pct": 18.0,
                    "put_call_oi_ratio": 1.7,
                },
            },
        },
        {},
    )

    types = {driver["type"] for driver in result["candidate_drivers"]}
    assert "technical_momentum_condition" in types
    assert "elevated_realized_volatility" in types
    assert "futures_basis_context" in types
    assert "futures_open_interest_change" in types
    assert "options_positioning_context" in types
    assert result["causality_status"] == "candidate_explanation_not_proven_causality"
    assert "not proof of cause" in result["note"]
