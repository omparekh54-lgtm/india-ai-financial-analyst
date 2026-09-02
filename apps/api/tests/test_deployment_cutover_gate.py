import json
from pathlib import Path

import pytest

from app.core.deployment_cutover import (
    DEPLOYMENT_CUTOVER_PHASE_ORDER,
    evaluate_deployment_cutover,
    required_deployment_cutover_contract,
)
from scripts.run_deployment_cutover_gate import _load_json, main


def _complete_evidence() -> dict[str, object]:
    return {
        "railway_backend_runtime": {
            "project_id": "faaeb5ad-425e-4a7d-b264-c4c56982890b",
            "environment_id": "1c6fde7d-3e37-40fa-ae30-415ddbcac967",
            "api_service_id": "b0fc5020-6a62-4fa9-a9ea-c49802aff09e",
            "api_deployment_status": "SUCCESS",
            "worker_service_success_count": 3,
            "required_service_count": 4,
            "api_domain": "api-production-d331d.up.railway.app",
            "api_domain_https": True,
            "api_target_port": 8000,
            "api_health_status": 200,
            "api_ready_status": 200,
            "api_ready_database_healthy": True,
            "api_runtime_error_count": 0,
        },
        "vercel_frontend_backend_wiring": {
            "team_id": "team_6SL2MJ0RqbyM1rn3BaK1fcEo",
            "project_id": "prj_dUze0HZPjImn2Gw360Pa4GdnIbhm",
            "git_repository": "omparekh54-lgtm/india-ai-financial-analyst",
            "preview_deployment_id": "dpl_ACtaB6oNK6UwAGEZXsN66hMfWryd",
            "preview_deployment_ready": True,
            "preview_commit_sha": "236e6ec84ebdb5fc8358a8063eb36068cfe4290b",
            "branch_alias": "india-ai-financial-analyst-git-16e4ca-omparekh54-4085s-projects.vercel.app",
            "env_file_detected": True,
            "api_base_url": "https://api-production-d331d.up.railway.app",
            "api_base_url_matches_railway": True,
            "frontend_runtime_error_count": 0,
        },
        "release_promotion_controls": {
            "pull_request_number": 1,
            "source_branch": "feat/foundation-v1",
            "target_branch": "main",
            "latest_preview_commit_sha": "236e6ec84ebdb5fc8358a8063eb36068cfe4290b",
            "production_branch_matches_target": True,
            "required_checks_green": True,
            "manual_promotion_required": True,
            "rollback_candidate_available": True,
            "commercial_launch_gate_disabled_until_corpus_ready": True,
            "no_service_role_key_in_frontend": True,
        },
        "live_acceptance_evidence": {
            "production_url": "https://india-ai-financial-analyst.vercel.app",
            "production_url_https": True,
            "post_promotion_smoke_required": True,
            "phase_25_30_release_gate_status": "blocked_by_corpus",
            "phase_31_36_post_launch_gate_status": "pending_until_production_promotion",
            "phase_43_46_activation_gate_status": "blocked_by_corpus_or_security_exception",
            "known_blockers_recorded": True,
            "corpus_not_marked_complete_without_real_data": True,
            "operator_acceptance_reference": "operator-approved-preview-cutover-2026-09-02",
        },
    }


def test_deployment_cutover_passes_with_complete_non_secret_evidence() -> None:
    report = evaluate_deployment_cutover(_complete_evidence())

    assert report.ready is True
    assert tuple(phase.name for phase in report.phases) == DEPLOYMENT_CUTOVER_PHASE_ORDER
    assert report.failed_phases == ()


def test_deployment_cutover_fails_closed_for_missing_evidence() -> None:
    report = evaluate_deployment_cutover({})

    assert report.ready is False
    assert report.failed_phases == DEPLOYMENT_CUTOVER_PHASE_ORDER
    assert all(phase.errors for phase in report.phases)


def test_phase_47_requires_live_railway_success_and_ready_database() -> None:
    evidence = _complete_evidence()
    railway = dict(evidence["railway_backend_runtime"])  # type: ignore[index]
    railway["api_deployment_status"] = "FAILED"
    railway["api_ready_database_healthy"] = False
    evidence["railway_backend_runtime"] = railway

    report = evaluate_deployment_cutover(evidence)
    phase = next(item for item in report.phases if item.name == "phase_47_railway_backend_runtime")

    assert report.ready is False
    assert "railway_api_deployment_not_success" in phase.errors
    assert "railway_ready_database_not_healthy" in phase.errors


def test_phase_48_requires_vercel_to_use_railway_api_url() -> None:
    evidence = _complete_evidence()
    vercel = dict(evidence["vercel_frontend_backend_wiring"])  # type: ignore[index]
    vercel["api_base_url"] = "http://localhost:8000"
    vercel["api_base_url_matches_railway"] = False
    evidence["vercel_frontend_backend_wiring"] = vercel

    report = evaluate_deployment_cutover(evidence)
    phase = next(
        item for item in report.phases if item.name == "phase_48_vercel_frontend_backend_wiring"
    )

    assert report.ready is False
    assert "vercel_api_base_url_not_https" in phase.errors
    assert "vercel_api_base_url_not_wired_to_railway" in phase.errors


def test_phase_49_requires_manual_promotion_and_disabled_commercial_launch() -> None:
    evidence = _complete_evidence()
    controls = dict(evidence["release_promotion_controls"])  # type: ignore[index]
    controls["manual_promotion_required"] = False
    controls["commercial_launch_gate_disabled_until_corpus_ready"] = False
    evidence["release_promotion_controls"] = controls

    report = evaluate_deployment_cutover(evidence)
    phase = next(item for item in report.phases if item.name == "phase_49_release_promotion_controls")

    assert report.ready is False
    assert "manual_promotion_required_missing_or_false" in phase.errors
    assert "commercial_launch_gate_disabled_until_corpus_ready_missing_or_false" in phase.errors


def test_phase_50_preserves_corpus_and_post_promotion_acceptance_boundaries() -> None:
    evidence = _complete_evidence()
    acceptance = dict(evidence["live_acceptance_evidence"])  # type: ignore[index]
    acceptance["phase_25_30_release_gate_status"] = "passed"
    acceptance["phase_31_36_post_launch_gate_status"] = "not_run"
    acceptance["corpus_not_marked_complete_without_real_data"] = False
    evidence["live_acceptance_evidence"] = acceptance

    report = evaluate_deployment_cutover(evidence)
    phase = next(item for item in report.phases if item.name == "phase_50_live_acceptance_evidence")

    assert report.ready is False
    assert "phase_31_36_post_launch_gate_status_missing" in phase.errors
    assert "corpus_completion_policy_not_preserved" in phase.errors


def test_required_contract_names_all_phase_47_50_evidence_groups() -> None:
    contract = required_deployment_cutover_contract()

    assert contract["phase_order"] == list(DEPLOYMENT_CUTOVER_PHASE_ORDER)
    assert set(contract["requires"]) == {
        "railway_backend_runtime",
        "vercel_frontend_backend_wiring",
        "release_promotion_controls",
        "live_acceptance_evidence",
    }


def test_deployment_cutover_cli_plan_only(capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    old_argv = sys.argv
    sys.argv = ["run_deployment_cutover_gate.py", "--plan-only"]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["phase_order"] == list(DEPLOYMENT_CUTOVER_PHASE_ORDER)


def test_deployment_cutover_cli_evidence_file(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    import sys

    evidence_path = tmp_path / "deployment-cutover-evidence.json"
    evidence_path.write_text(json.dumps(_complete_evidence()), encoding="utf-8")

    old_argv = sys.argv
    sys.argv = [
        "run_deployment_cutover_gate.py",
        "--evidence-json",
        str(evidence_path),
    ]
    try:
        assert main() == 0
    finally:
        sys.argv = old_argv

    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True


def test_deployment_cutover_rejects_secret_like_evidence_keys(tmp_path: Path) -> None:
    evidence = _complete_evidence()
    evidence["railway_backend_runtime"] = {
        **evidence["railway_backend_runtime"],  # type: ignore[arg-type]
        "database_url": "must-not-be-here",
        "service_role_key": "must-not-be-here",
    }
    evidence_path = tmp_path / "bad-cutover-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(ValueError, match="secret-like keys"):
        _load_json(evidence_path)
