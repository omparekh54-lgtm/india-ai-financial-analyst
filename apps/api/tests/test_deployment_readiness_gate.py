import json
from pathlib import Path

import pytest

from app.core.deployment_readiness import (
    DEPLOYMENT_READINESS_PHASE_ORDER,
    LATEST_REQUIRED_MIGRATION,
    evaluate_deployment_readiness,
    required_deployment_readiness_contract,
)
from scripts.run_deployment_readiness_gate import _load_json, main


def _complete_evidence() -> dict[str, object]:
    return {
        "vercel_project": {
            "team_id": "team_123",
            "project_id": "prj_123",
            "project_name": "india-ai-financial-analyst",
            "git_repository": "omparekh54-lgtm/india-ai-financial-analyst",
            "production_branch": "main",
            "root_directory": "apps/web",
            "framework": "nextjs",
            "preview_deployments_enabled": True,
        },
        "environment_contract": {
            "vercel_token_configured": True,
            "vercel_org_id_configured": True,
            "vercel_project_id_configured": True,
            "database_url_configured": True,
            "supabase_url_configured": True,
            "supabase_publishable_key_configured": True,
            "api_base_url_configured": True,
            "web_app_url_configured": True,
            "cors_origins_https_only": True,
            "no_plaintext_credentials_in_repo": True,
        },
        "database_release": {
            "supabase_project_healthy": True,
            "migrations_applied_through": LATEST_REQUIRED_MIGRATION,
            "latest_required_migration": LATEST_REQUIRED_MIGRATION,
            "rls_no_policy_count": 0,
            "security_warn_count": 1,
            "managed_platform_warnings_accepted": True,
        },
        "build_artifacts": {
            "github_ci_success": True,
            "api_ruff_passed": True,
            "api_typecheck_passed": True,
            "api_tests_passed": True,
            "web_build_passed": True,
            "production_compose_valid": True,
            "dockerfile_valid": True,
            "deployment_commit_sha": "68b4a24",
        },
        "auth_traffic": {
            "supabase_auth_site_url_configured": True,
            "redirect_urls_configured": True,
            "owner_test_user_ready": True,
            "other_test_user_ready": True,
            "smoke_user_ready": True,
            "rate_limits_configured": True,
            "deployment_smoke_plan_ready": True,
        },
        "final_runbook": {
            "phase_30_gate_plan_ready": True,
            "phase_31_36_gate_plan_ready": True,
            "rollback_plan_ready": True,
            "dns_cutover_plan_ready": True,
            "commercial_approval_plan_ready": True,
            "launch_owner_named": True,
        },
    }


def test_deployment_readiness_passes_with_complete_non_secret_evidence() -> None:
    report = evaluate_deployment_readiness(_complete_evidence())

    assert report.ready is True
    assert tuple(phase.name for phase in report.phases) == DEPLOYMENT_READINESS_PHASE_ORDER
    assert report.failed_phases == ()


def test_deployment_readiness_fails_closed_for_missing_evidence() -> None:
    report = evaluate_deployment_readiness({})

    assert report.ready is False
    assert report.failed_phases == DEPLOYMENT_READINESS_PHASE_ORDER
    assert all(phase.errors for phase in report.phases)


def test_deployment_readiness_requires_actual_vercel_project_identity() -> None:
    evidence = _complete_evidence()
    vercel_project = dict(evidence["vercel_project"])  # type: ignore[index]
    vercel_project["project_id"] = ""
    vercel_project["team_id"] = ""
    evidence["vercel_project"] = vercel_project

    report = evaluate_deployment_readiness(evidence)
    phase = next(
        item for item in report.phases if item.name == "phase_37_vercel_project_linkage"
    )

    assert report.ready is False
    assert "vercel_project_id_missing_or_invalid" in phase.errors
    assert "vercel_team_id_missing_or_invalid" in phase.errors


def test_deployment_readiness_requires_database_migration_and_warning_acceptance() -> None:
    evidence = _complete_evidence()
    database_release = dict(evidence["database_release"])  # type: ignore[index]
    database_release["migrations_applied_through"] = "0025_usage_and_commercial_controls.sql"
    database_release["managed_platform_warnings_accepted"] = False
    evidence["database_release"] = database_release

    report = evaluate_deployment_readiness(evidence)
    phase = next(
        item for item in report.phases if item.name == "phase_39_database_migration_readiness"
    )

    assert report.ready is False
    assert "required_migration_not_applied" in phase.errors
    assert "unaccepted_security_warnings_present" in phase.errors


def test_required_contract_names_all_phase_37_42_evidence_groups() -> None:
    contract = required_deployment_readiness_contract()

    assert contract["phase_order"] == list(DEPLOYMENT_READINESS_PHASE_ORDER)
    assert set(contract["requires"]) == {
        "vercel_project",
        "environment_contract",
        "database_release",
        "build_artifacts",
        "auth_traffic",
        "final_runbook",
    }


def test_deployment_readiness_cli_plan_only(capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    old_argv = sys.argv
    sys.argv = ["run_deployment_readiness_gate.py", "--plan-only"]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["phase_order"] == list(DEPLOYMENT_READINESS_PHASE_ORDER)


def test_deployment_readiness_cli_evidence_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    evidence_path = tmp_path / "deployment-readiness-evidence.json"
    evidence_path.write_text(json.dumps(_complete_evidence()), encoding="utf-8")

    old_argv = sys.argv
    sys.argv = [
        "run_deployment_readiness_gate.py",
        "--evidence-json",
        str(evidence_path),
    ]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True


def test_deployment_readiness_rejects_secret_like_evidence_keys(tmp_path: Path) -> None:
    evidence = _complete_evidence()
    evidence["environment_contract"] = {
        **evidence["environment_contract"],  # type: ignore[arg-type]
        "vercel_access_token": "must-not-be-here",
    }
    evidence_path = tmp_path / "bad-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="secret-like keys"):
        _load_json(evidence_path)
