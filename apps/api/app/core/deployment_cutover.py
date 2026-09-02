from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEPLOYMENT_CUTOVER_PHASE_ORDER = (
    "phase_47_railway_backend_runtime",
    "phase_48_vercel_frontend_backend_wiring",
    "phase_49_release_promotion_controls",
    "phase_50_live_acceptance_evidence",
)

EXPECTED_REPOSITORY = "omparekh54-lgtm/india-ai-financial-analyst"
EXPECTED_API_HEALTH_PATH = "/health"
EXPECTED_API_READY_PATH = "/ready"
MIN_REQUIRED_RAILWAY_SERVICES = 4
MIN_READY_HTTP_STATUS = 200


@dataclass(frozen=True)
class DeploymentCutoverPhase:
    name: str
    ready: bool
    errors: tuple[str, ...]
    diagnostics: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ready": self.ready,
            "errors": list(self.errors),
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True)
class DeploymentCutoverReport:
    ready: bool
    phases: tuple[DeploymentCutoverPhase, ...]

    @property
    def failed_phases(self) -> tuple[str, ...]:
        return tuple(phase.name for phase in self.phases if not phase.ready)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "phase_order": list(DEPLOYMENT_CUTOVER_PHASE_ORDER),
            "failed_phases": list(self.failed_phases),
            "phases": [phase.as_dict() for phase in self.phases],
        }


def evaluate_deployment_cutover(evidence: Mapping[str, Any]) -> DeploymentCutoverReport:
    phases = (
        _phase_47_railway_backend_runtime(_mapping(evidence.get("railway_backend_runtime"))),
        _phase_48_vercel_frontend_backend_wiring(
            _mapping(evidence.get("vercel_frontend_backend_wiring"))
        ),
        _phase_49_release_promotion_controls(_mapping(evidence.get("release_promotion_controls"))),
        _phase_50_live_acceptance_evidence(_mapping(evidence.get("live_acceptance_evidence"))),
    )
    return DeploymentCutoverReport(
        ready=all(phase.ready for phase in phases),
        phases=phases,
    )


def required_deployment_cutover_contract() -> dict[str, object]:
    return {
        "phase_order": list(DEPLOYMENT_CUTOVER_PHASE_ORDER),
        "requires": {
            "railway_backend_runtime": [
                "project_id",
                "environment_id",
                "api_service_id",
                "api_deployment_status",
                "worker_service_success_count",
                "required_service_count",
                "api_domain",
                "api_domain_https",
                "api_target_port",
                "api_health_status",
                "api_ready_status",
                "api_ready_database_healthy",
                "api_runtime_error_count",
            ],
            "vercel_frontend_backend_wiring": [
                "team_id",
                "project_id",
                "git_repository",
                "preview_deployment_id",
                "preview_deployment_ready",
                "preview_commit_sha",
                "branch_alias",
                "env_file_detected",
                "api_base_url",
                "api_base_url_matches_railway",
                "frontend_runtime_error_count",
            ],
            "release_promotion_controls": [
                "pull_request_number",
                "source_branch",
                "target_branch",
                "latest_preview_commit_sha",
                "production_branch_matches_target",
                "required_checks_green",
                "manual_promotion_required",
                "rollback_candidate_available",
                "commercial_launch_gate_disabled_until_corpus_ready",
                "no_service_role_key_in_frontend",
            ],
            "live_acceptance_evidence": [
                "production_url",
                "production_url_https",
                "post_promotion_smoke_required",
                "phase_25_30_release_gate_status",
                "phase_31_36_post_launch_gate_status",
                "phase_43_46_activation_gate_status",
                "known_blockers_recorded",
                "corpus_not_marked_complete_without_real_data",
                "operator_acceptance_reference",
            ],
        },
        "policy": "missing_or_placeholder_cutover_evidence_fails_closed",
    }


def _phase_47_railway_backend_runtime(data: Mapping[str, Any]) -> DeploymentCutoverPhase:
    errors: list[str] = []
    if not _text(data.get("project_id")):
        errors.append("railway_project_id_missing")
    if not _text(data.get("environment_id")):
        errors.append("railway_environment_id_missing")
    if not _text(data.get("api_service_id")):
        errors.append("railway_api_service_id_missing")
    if _text(data.get("api_deployment_status")).upper() != "SUCCESS":
        errors.append("railway_api_deployment_not_success")
    if (
        _number(data.get("worker_service_success_count"), default=0)
        < _number(data.get("required_service_count"), default=MIN_REQUIRED_RAILWAY_SERVICES) - 1
    ):
        errors.append("railway_worker_services_not_all_success")
    if not _text(data.get("api_domain")).endswith(".up.railway.app"):
        errors.append("railway_api_domain_missing_or_unrecognized")
    if not _true(data.get("api_domain_https")):
        errors.append("railway_api_domain_not_https")
    if _number(data.get("api_target_port"), default=0) != 8000:
        errors.append("railway_api_target_port_not_8000")
    if _number(data.get("api_health_status"), default=0) != MIN_READY_HTTP_STATUS:
        errors.append("railway_health_check_not_200")
    if _number(data.get("api_ready_status"), default=0) != MIN_READY_HTTP_STATUS:
        errors.append("railway_ready_check_not_200")
    if not _true(data.get("api_ready_database_healthy")):
        errors.append("railway_ready_database_not_healthy")
    if _number(data.get("api_runtime_error_count"), default=1) != 0:
        errors.append("railway_runtime_errors_present")
    return _phase("phase_47_railway_backend_runtime", errors, data)


def _phase_48_vercel_frontend_backend_wiring(data: Mapping[str, Any]) -> DeploymentCutoverPhase:
    errors: list[str] = []
    if not _text(data.get("team_id")).startswith("team_"):
        errors.append("vercel_team_id_missing_or_invalid")
    if not _text(data.get("project_id")).startswith("prj_"):
        errors.append("vercel_project_id_missing_or_invalid")
    if _text(data.get("git_repository")) != EXPECTED_REPOSITORY:
        errors.append("vercel_project_not_linked_to_expected_repository")
    if not _text(data.get("preview_deployment_id")).startswith("dpl_"):
        errors.append("vercel_preview_deployment_id_missing_or_invalid")
    if not _true(data.get("preview_deployment_ready")):
        errors.append("vercel_preview_not_ready")
    if not _text(data.get("preview_commit_sha")):
        errors.append("vercel_preview_commit_sha_missing")
    if not _text(data.get("branch_alias")).endswith(".vercel.app"):
        errors.append("vercel_branch_alias_missing_or_invalid")
    if not _true(data.get("env_file_detected")):
        errors.append("vercel_env_production_file_not_detected")
    api_base_url = _text(data.get("api_base_url"))
    if not api_base_url.startswith("https://"):
        errors.append("vercel_api_base_url_not_https")
    if not _true(data.get("api_base_url_matches_railway")):
        errors.append("vercel_api_base_url_not_wired_to_railway")
    if _number(data.get("frontend_runtime_error_count"), default=1) != 0:
        errors.append("vercel_frontend_runtime_errors_present")
    return _phase("phase_48_vercel_frontend_backend_wiring", errors, data)


def _phase_49_release_promotion_controls(data: Mapping[str, Any]) -> DeploymentCutoverPhase:
    errors: list[str] = []
    if _number(data.get("pull_request_number"), default=0) < 1:
        errors.append("pull_request_number_missing")
    if _text(data.get("source_branch")) == _text(data.get("target_branch")):
        errors.append("source_and_target_branch_must_differ")
    if _text(data.get("target_branch")) not in {"main", "master"}:
        errors.append("target_branch_must_be_main_or_master")
    if not _text(data.get("latest_preview_commit_sha")):
        errors.append("latest_preview_commit_sha_missing")
    for key in (
        "production_branch_matches_target",
        "required_checks_green",
        "manual_promotion_required",
        "rollback_candidate_available",
        "commercial_launch_gate_disabled_until_corpus_ready",
        "no_service_role_key_in_frontend",
    ):
        if not _true(data.get(key)):
            errors.append(f"{key}_missing_or_false")
    return _phase("phase_49_release_promotion_controls", errors, data)


def _phase_50_live_acceptance_evidence(data: Mapping[str, Any]) -> DeploymentCutoverPhase:
    errors: list[str] = []
    if not _text(data.get("production_url")).startswith("https://"):
        errors.append("production_url_missing_or_not_https")
    if not _true(data.get("production_url_https")):
        errors.append("production_url_https_not_verified")
    if not _true(data.get("post_promotion_smoke_required")):
        errors.append("post_promotion_smoke_not_required")
    if _text(data.get("phase_25_30_release_gate_status")).lower() not in {"passed", "blocked_by_corpus"}:
        errors.append("phase_25_30_release_gate_status_missing")
    if _text(data.get("phase_31_36_post_launch_gate_status")).lower() not in {
        "passed",
        "pending_until_production_promotion",
    }:
        errors.append("phase_31_36_post_launch_gate_status_missing")
    if _text(data.get("phase_43_46_activation_gate_status")).lower() not in {
        "passed",
        "blocked_by_corpus_or_security_exception",
    }:
        errors.append("phase_43_46_activation_gate_status_missing")
    if not _true(data.get("known_blockers_recorded")):
        errors.append("known_blockers_not_recorded")
    if not _true(data.get("corpus_not_marked_complete_without_real_data")):
        errors.append("corpus_completion_policy_not_preserved")
    if not _text(data.get("operator_acceptance_reference")):
        errors.append("operator_acceptance_reference_missing")
    return _phase("phase_50_live_acceptance_evidence", errors, data)


def _phase(
    name: str,
    errors: list[str],
    diagnostics: Mapping[str, Any],
) -> DeploymentCutoverPhase:
    return DeploymentCutoverPhase(
        name=name,
        ready=not errors,
        errors=tuple(errors),
        diagnostics=dict(diagnostics),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _true(value: object) -> bool:
    return value is True


def _number(value: object, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default
