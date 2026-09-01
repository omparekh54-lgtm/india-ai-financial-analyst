import json
from pathlib import Path

import pytest

from app.core.production_activation import (
    LATEST_REQUIRED_MIGRATION,
    MIN_REAL_SECURITIES,
    PRODUCTION_ACTIVATION_PHASE_ORDER,
    evaluate_production_activation,
    required_production_activation_contract,
)
from scripts.run_production_activation_gate import _load_json, main


def _complete_evidence() -> dict[str, object]:
    return {
        "vercel_live_project": {
            "team_id": "team_6SL2MJ0RqbyM1rn3BaK1fcEo",
            "project_id": "prj_123",
            "project_name": "india-ai-financial-analyst",
            "git_repository": "omparekh54-lgtm/india-ai-financial-analyst",
            "production_branch": "main",
            "root_directory": "apps/web",
            "framework": "nextjs",
            "preview_deployment_url": "https://india-ai-financial-analyst-preview.vercel.app",
            "preview_deployment_ready": True,
            "production_domains_https_ready": True,
        },
        "production_environment": {
            "vercel_env_pulled": True,
            "vercel_org_id_configured": True,
            "vercel_project_id_configured": True,
            "database_url_configured": True,
            "supabase_url_configured": True,
            "supabase_publishable_key_configured": True,
            "api_base_url_configured": True,
            "web_app_url_configured": True,
            "cors_origins_https_only": True,
            "frontend_uses_publishable_key_only": True,
            "service_role_absent_from_frontend": True,
            "no_plaintext_credentials_in_repo": True,
        },
        "supabase_real_data": {
            "securities_count": MIN_REAL_SECURITIES,
            "market_bars_count": 200000,
            "evidence_chunks_count": 5000,
            "research_jobs_count": 5,
            "research_reports_count": 5,
            "representative_research_jobs_count": 5,
            "agent_15_completed_count": 5,
            "agent_16_completed_count": 5,
            "non_production_source_rows": 0,
            "production_corpus_gate_ready": True,
        },
        "supabase_security": {
            "project_status": "ACTIVE_HEALTHY",
            "database_postgres_version": "17.6.1.166",
            "migrations_applied_through": LATEST_REQUIRED_MIGRATION,
            "latest_required_migration": LATEST_REQUIRED_MIGRATION,
            "rls_no_policy_count": 0,
            "security_error_count": 0,
            "security_warn_count": 1,
            "managed_platform_warnings_accepted": True,
            "pg_net_public_exception_status": "accepted",
            "performance_blocker_count": 0,
        },
    }


def test_production_activation_passes_with_complete_non_secret_evidence() -> None:
    report = evaluate_production_activation(_complete_evidence())

    assert report.ready is True
    assert tuple(phase.name for phase in report.phases) == PRODUCTION_ACTIVATION_PHASE_ORDER
    assert report.failed_phases == ()


def test_production_activation_fails_closed_for_missing_evidence() -> None:
    report = evaluate_production_activation({})

    assert report.ready is False
    assert report.failed_phases == PRODUCTION_ACTIVATION_PHASE_ORDER
    assert all(phase.errors for phase in report.phases)


def test_production_activation_requires_expected_vercel_repo_link() -> None:
    evidence = _complete_evidence()
    vercel_project = dict(evidence["vercel_live_project"])  # type: ignore[index]
    vercel_project["git_repository"] = "someone/else"
    evidence["vercel_live_project"] = vercel_project

    report = evaluate_production_activation(evidence)
    phase = next(item for item in report.phases if item.name == "phase_43_vercel_live_project")

    assert report.ready is False
    assert "vercel_project_not_linked_to_expected_repository" in phase.errors


def test_production_activation_reflects_current_empty_live_corpus() -> None:
    evidence = _complete_evidence()
    evidence["supabase_real_data"] = {
        "securities_count": 2302,
        "market_bars_count": 0,
        "evidence_chunks_count": 0,
        "research_jobs_count": 0,
        "research_reports_count": 0,
        "representative_research_jobs_count": 0,
        "agent_15_completed_count": 0,
        "agent_16_completed_count": 0,
        "non_production_source_rows": 0,
        "production_corpus_gate_ready": False,
    }

    report = evaluate_production_activation(evidence)
    phase = next(
        item for item in report.phases if item.name == "phase_45_supabase_real_data_readiness"
    )

    assert report.ready is False
    assert "market_bars_missing" in phase.errors
    assert "evidence_chunks_missing" in phase.errors
    assert "research_jobs_missing" in phase.errors
    assert "production_corpus_gate_not_ready" in phase.errors


def test_production_activation_requires_pg_net_exception_for_security_warn() -> None:
    evidence = _complete_evidence()
    security = dict(evidence["supabase_security"])  # type: ignore[index]
    security["managed_platform_warnings_accepted"] = False
    security["pg_net_public_exception_status"] = "pending"
    evidence["supabase_security"] = security

    report = evaluate_production_activation(evidence)
    phase = next(
        item for item in report.phases if item.name == "phase_46_supabase_security_finalization"
    )

    assert report.ready is False
    assert "unaccepted_security_warnings_present" in phase.errors
    assert "pg_net_public_exception_not_accepted" in phase.errors


def test_required_contract_names_all_phase_43_46_evidence_groups() -> None:
    contract = required_production_activation_contract()

    assert contract["phase_order"] == list(PRODUCTION_ACTIVATION_PHASE_ORDER)
    assert set(contract["requires"]) == {
        "vercel_live_project",
        "production_environment",
        "supabase_real_data",
        "supabase_security",
    }


def test_production_activation_cli_plan_only(capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    old_argv = sys.argv
    sys.argv = ["run_production_activation_gate.py", "--plan-only"]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["phase_order"] == list(PRODUCTION_ACTIVATION_PHASE_ORDER)


def test_production_activation_cli_evidence_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    evidence_path = tmp_path / "production-activation-evidence.json"
    evidence_path.write_text(json.dumps(_complete_evidence()), encoding="utf-8")

    old_argv = sys.argv
    sys.argv = [
        "run_production_activation_gate.py",
        "--evidence-json",
        str(evidence_path),
    ]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True


def test_production_activation_rejects_secret_like_evidence_keys(tmp_path: Path) -> None:
    evidence = _complete_evidence()
    evidence["production_environment"] = {
        **evidence["production_environment"],  # type: ignore[arg-type]
        "database_password": "must-not-be-here",
    }
    evidence_path = tmp_path / "bad-activation-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="secret-like keys"):
        _load_json(evidence_path)
