import json
from pathlib import Path

import pytest

from app.core.production_promotion import (
    PRODUCTION_PROMOTION_PHASE_ORDER,
    evaluate_production_promotion,
    required_production_promotion_contract,
)
from scripts.run_production_promotion_gate import _load_json, main


def _complete_evidence() -> dict[str, object]:
    promoted_sha = "bb47e04d52ce30fe2e6284f210c362f82c66f96d"
    return {
        "production_branch_promotion": {
            "pull_request_number": 1,
            "git_repository": "omparekh54-lgtm/india-ai-financial-analyst",
            "source_branch": "feat/foundation-v1",
            "target_branch": "main",
            "expected_head_sha": promoted_sha,
            "required_checks_green": True,
            "review_or_owner_approval_recorded": True,
            "protected_environment_approval_recorded": True,
            "merge_strategy": "squash",
            "deployment_cutover_gate_ready": True,
            "no_uncommitted_config_drift": True,
        },
        "vercel_production_domain": {
            "production_deployment_id": "dpl_ProductionReady51to54",
            "production_deployment_ready": True,
            "production_commit_sha": promoted_sha,
            "promoted_commit_sha": promoted_sha,
            "production_url": "https://india-ai-financial-analyst.vercel.app",
            "production_url_https": True,
            "production_alias_ready": True,
            "root_directory": "apps/web",
            "framework": "Next.js",
            "env_file_detected": True,
            "api_base_url": "https://api-production-d331d.up.railway.app",
            "runtime_error_count": 0,
        },
        "backend_post_promotion_sync": {
            "railway_project_id": "faaeb5ad-425e-4a7d-b264-c4c56982890b",
            "railway_environment_id": "1c6fde7d-3e37-40fa-ae30-415ddbcac967",
            "railway_api_deployment_status": "SUCCESS",
            "railway_worker_success_count": 3,
            "railway_branch_strategy": "feature_branch_until_final_merge",
            "railway_source_branch_matches_promoted": False,
            "api_health_status": 200,
            "api_ready_status": 200,
            "api_ready_database_healthy": True,
            "cors_includes_production_url": True,
            "runtime_blocker_count": 0,
            "free_only_enabled": True,
            "commercial_launch_disabled": True,
        },
        "launch_decision_register": {
            "launch_decision": "conditional_go",
            "known_blockers_recorded": True,
            "corpus_gate_status": "blocked_by_real_data",
            "supabase_security_exception_status": "pending",
            "sentry_monitoring_status": "accepted_deferred",
            "npm_audit_status": "accepted_deferred",
            "rollback_reference": "docs/PHASE_47_50_DEPLOYMENT_CUTOVER.md",
            "monitoring_reference": "docs/PRODUCTION_QA.md",
            "operator_signoff_reference": "operator-approved-production-promotion-2026-09-02",
            "conditional_approval_reference": "launch-register-conditional-go-2026-09-02",
        },
    }


def test_production_promotion_passes_with_complete_conditional_evidence() -> None:
    report = evaluate_production_promotion(_complete_evidence())

    assert report.ready is True
    assert tuple(phase.name for phase in report.phases) == PRODUCTION_PROMOTION_PHASE_ORDER
    assert report.failed_phases == ()


def test_production_promotion_fails_closed_for_missing_evidence() -> None:
    report = evaluate_production_promotion({})

    assert report.ready is False
    assert report.failed_phases == PRODUCTION_PROMOTION_PHASE_ORDER
    assert all(phase.errors for phase in report.phases)


def test_phase_51_requires_clean_checks_and_approval() -> None:
    evidence = _complete_evidence()
    promotion = dict(evidence["production_branch_promotion"])  # type: ignore[index]
    promotion["required_checks_green"] = False
    promotion["protected_environment_approval_recorded"] = False
    evidence["production_branch_promotion"] = promotion

    report = evaluate_production_promotion(evidence)
    phase = next(
        item for item in report.phases if item.name == "phase_51_production_branch_promotion"
    )

    assert report.ready is False
    assert "required_checks_green_missing_or_false" in phase.errors
    assert "protected_environment_approval_recorded_missing_or_false" in phase.errors


def test_phase_52_requires_production_domain_and_promoted_commit_match() -> None:
    evidence = _complete_evidence()
    vercel = dict(evidence["vercel_production_domain"])  # type: ignore[index]
    vercel["production_commit_sha"] = "different-sha"
    vercel["api_base_url"] = "https://wrong-api.example.com"
    evidence["vercel_production_domain"] = vercel

    report = evaluate_production_promotion(evidence)
    phase = next(item for item in report.phases if item.name == "phase_52_vercel_production_domain")

    assert report.ready is False
    assert "production_commit_sha_does_not_match_promoted_commit" in phase.errors
    assert "vercel_api_base_url_not_expected_railway_url" in phase.errors


def test_phase_53_requires_backend_ready_cors_and_disabled_commercial_launch() -> None:
    evidence = _complete_evidence()
    backend = dict(evidence["backend_post_promotion_sync"])  # type: ignore[index]
    backend["cors_includes_production_url"] = False
    backend["commercial_launch_disabled"] = False
    evidence["backend_post_promotion_sync"] = backend

    report = evaluate_production_promotion(evidence)
    phase = next(
        item for item in report.phases if item.name == "phase_53_backend_post_promotion_sync"
    )

    assert report.ready is False
    assert "cors_does_not_include_production_url" in phase.errors
    assert "commercial_launch_not_disabled" in phase.errors


def test_phase_54_go_decision_requires_clean_launch_prerequisites() -> None:
    evidence = _complete_evidence()
    decision = dict(evidence["launch_decision_register"])  # type: ignore[index]
    decision["launch_decision"] = "go"
    decision["corpus_gate_status"] = "blocked_by_real_data"
    decision["supabase_security_exception_status"] = "pending"
    decision["sentry_monitoring_status"] = "accepted_deferred"
    decision["npm_audit_status"] = "accepted_deferred"
    evidence["launch_decision_register"] = decision

    report = evaluate_production_promotion(evidence)
    phase = next(
        item for item in report.phases if item.name == "phase_54_launch_decision_register"
    )

    assert report.ready is False
    assert "go_decision_requires_passed_corpus_gate" in phase.errors
    assert "go_decision_requires_resolved_supabase_security_status" in phase.errors
    assert "go_decision_requires_configured_sentry_monitoring" in phase.errors
    assert "go_decision_requires_clean_npm_audit" in phase.errors


def test_required_contract_names_all_phase_51_54_evidence_groups() -> None:
    contract = required_production_promotion_contract()

    assert contract["phase_order"] == list(PRODUCTION_PROMOTION_PHASE_ORDER)
    assert set(contract["requires"]) == {
        "production_branch_promotion",
        "vercel_production_domain",
        "backend_post_promotion_sync",
        "launch_decision_register",
    }


def test_production_promotion_cli_plan_only(capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    old_argv = sys.argv
    sys.argv = ["run_production_promotion_gate.py", "--plan-only"]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["phase_order"] == list(PRODUCTION_PROMOTION_PHASE_ORDER)


def test_production_promotion_cli_evidence_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    evidence_path = tmp_path / "production-promotion-evidence.json"
    evidence_path.write_text(json.dumps(_complete_evidence()), encoding="utf-8")

    old_argv = sys.argv
    sys.argv = [
        "run_production_promotion_gate.py",
        "--evidence-json",
        str(evidence_path),
    ]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True


def test_production_promotion_rejects_secret_like_evidence_keys(tmp_path: Path) -> None:
    evidence = _complete_evidence()
    evidence["backend_post_promotion_sync"] = {
        **evidence["backend_post_promotion_sync"],  # type: ignore[arg-type]
        "database_url": "must-not-be-here",
        "service_role_key": "must-not-be-here",
    }
    evidence_path = tmp_path / "bad-production-promotion-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="secret-like keys"):
        _load_json(evidence_path)
