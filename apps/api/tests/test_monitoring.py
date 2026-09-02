from app.research.monitoring import compare_snapshots, delta_summary, thesis_hash


def _snapshot(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "thesis_hash": "a" * 64,
        "metrics": {
            "confidence": {"data_confidence": 0.80, "thesis_confidence": 0.70},
            "valuation": {"base_case_value": 100.0},
            "financials": {"revenue_growth": 0.10},
        },
        "catalysts": [],
        "risks": [],
    }
    value.update(overrides)
    return value


def test_first_snapshot_is_baseline_not_alert() -> None:
    delta = compare_snapshots(None, _snapshot())
    assert delta.baseline_available is False
    assert delta.changed is False
    assert delta_summary(delta) == "No material validated change detected"


def test_delta_detects_thesis_risk_confidence_and_metric_changes() -> None:
    previous = _snapshot(risks=[{"claim_id": "risk-1", "statement": "Old risk"}])
    current = _snapshot(
        thesis_hash="b" * 64,
        risks=[{"claim_id": "risk-2", "statement": "New risk"}],
        catalysts=[{"claim_id": "cat-1", "statement": "New catalyst"}],
        metrics={
            "confidence": {"data_confidence": 0.90, "thesis_confidence": 0.70},
            "valuation": {"base_case_value": 112.0},
            "financials": {"revenue_growth": 0.15},
        },
    )

    delta = compare_snapshots(previous, current)
    assert delta.changed is True
    assert delta.thesis_changed is True
    assert delta.severity == "high"
    assert [item["claim_id"] for item in delta.added_risks] == ["risk-2"]
    assert [item["claim_id"] for item in delta.resolved_risks] == ["risk-1"]
    assert [item["claim_id"] for item in delta.added_catalysts] == ["cat-1"]
    assert delta.confidence_changes[0]["metric"] == "data_confidence"
    assert {item["metric"] for item in delta.metric_changes} == {
        "financials.revenue_growth",
        "valuation.base_case_value",
    }
    assert "validated thesis changed" in delta_summary(delta)


def test_wording_change_does_not_duplicate_structured_claim() -> None:
    previous = _snapshot(
        risks=[{"claim_id": "risk-1", "statement": "Receivables increased materially"}]
    )
    current = _snapshot(
        risks=[{"claim_id": "risk-1", "statement": "Material receivables increase"}]
    )
    delta = compare_snapshots(previous, current)
    assert delta.added_risks == ()
    assert delta.resolved_risks == ()
    assert delta.changed is False


def test_thesis_hash_is_order_stable_for_mapping_keys() -> None:
    report_a = {
        "executive_summary": "Summary",
        "sections": {"financials": [{"statement": "Revenue grew"}], "risk": []},
    }
    report_b = {
        "sections": {"risk": [], "financials": [{"statement": "Revenue grew"}]},
        "executive_summary": "Summary",
    }
    assert thesis_hash(report_a) == thesis_hash(report_b)
