import json
from pathlib import Path

from app.core.post_launch_acceptance import (
    POST_LAUNCH_PHASE_ORDER,
    evaluate_post_launch_evidence,
    required_post_launch_evidence_contract,
)
from scripts.run_post_launch_acceptance_gate import main


def _complete_evidence() -> dict[str, object]:
    return {
        "observability": {
            "production_deployment_url": "https://india-ai.example.com",
            "vercel_project_id": "prj_123",
            "error_monitoring_configured": True,
            "privacy_filtering_enabled": True,
            "alert_routes": ["ops-primary"],
            "critical_runtime_error_count_24h": 0,
        },
        "data_freshness": {
            "corpus_ready": True,
            "stale_market_data_count": 0,
            "failed_ingestion_runs_24h": 0,
            "official_feed_lag_minutes": 30,
            "macro_series_present": 9,
            "benchmark_codes_with_bars": 2,
        },
        "research_quality": {
            "evaluated_reports": 25,
            "distinct_real_sectors": 4,
            "validated_claim_coverage": 0.92,
            "unsupported_claim_rate": 0.01,
            "validator_completed": True,
            "calibration_errors_open": 0,
        },
        "security": {
            "supabase_security_warn_count": 0,
            "rls_enabled_no_policy_count": 0,
            "critical_dependency_vulnerabilities": 0,
            "exposed_secret_findings": 0,
            "auth_isolation_passed": True,
            "security_review_approved": True,
        },
        "cost_quota": {
            "free_only": True,
            "paid_fallback_enabled": False,
            "monthly_budget_configured": True,
            "usage_caps_configured": True,
            "provider_quota_alerts_configured": True,
            "unapproved_paid_spend": 0,
        },
        "rollback_incident": {
            "production_backup_verified": True,
            "rollback_target_deployment_id": "dpl_123",
            "restore_drill_passed": True,
            "incident_runbook_url": "https://docs.example.com/incident-runbook",
            "on_call_route_configured": True,
            "release_owner_approved": True,
        },
    }


def test_post_launch_acceptance_passes_with_complete_real_evidence() -> None:
    report = evaluate_post_launch_evidence(_complete_evidence())

    assert report.ready is True
    assert tuple(phase.name for phase in report.phases) == POST_LAUNCH_PHASE_ORDER
    assert report.failed_phases == ()


def test_post_launch_acceptance_fails_closed_for_missing_evidence() -> None:
    report = evaluate_post_launch_evidence({})

    assert report.ready is False
    assert report.failed_phases == POST_LAUNCH_PHASE_ORDER
    assert all(phase.errors for phase in report.phases)


def test_post_launch_acceptance_rejects_optimistic_but_incomplete_security() -> None:
    evidence = _complete_evidence()
    security = dict(evidence["security"])  # type: ignore[index]
    security["rls_enabled_no_policy_count"] = 22
    security["security_review_approved"] = False
    evidence["security"] = security

    report = evaluate_post_launch_evidence(evidence)
    security_phase = next(
        phase for phase in report.phases if phase.name == "phase_34_security_acceptance"
    )

    assert report.ready is False
    assert "rls_enabled_tables_without_policies" in security_phase.errors
    assert "security_review_approval_missing" in security_phase.errors


def test_required_contract_names_all_phase_31_36_evidence_groups() -> None:
    contract = required_post_launch_evidence_contract()

    assert contract["phase_order"] == list(POST_LAUNCH_PHASE_ORDER)
    assert set(contract["requires"]) == {
        "observability",
        "data_freshness",
        "research_quality",
        "security",
        "cost_quota",
        "rollback_incident",
    }


def test_post_launch_cli_plan_only(capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    old_argv = sys.argv
    sys.argv = ["run_post_launch_acceptance_gate.py", "--plan-only"]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["phase_order"] == list(POST_LAUNCH_PHASE_ORDER)


def test_post_launch_cli_evidence_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    evidence_path = tmp_path / "post-launch-evidence.json"
    evidence_path.write_text(json.dumps(_complete_evidence()), encoding="utf-8")

    old_argv = sys.argv
    sys.argv = [
        "run_post_launch_acceptance_gate.py",
        "--evidence-json",
        str(evidence_path),
    ]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
