from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEPLOYMENT_READINESS_PHASE_ORDER = (
    "phase_37_vercel_project_linkage",
    "phase_38_environment_contract",
    "phase_39_database_migration_readiness",
    "phase_40_build_artifact_readiness",
    "phase_41_auth_traffic_readiness",
    "phase_42_final_deployment_runbook",
)

LATEST_REQUIRED_MIGRATION = "0026_backend_only_rls_deny_policies.sql"


@dataclass(frozen=True)
class DeploymentReadinessPhase:
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
class DeploymentReadinessReport:
    ready: bool
    phases: tuple[DeploymentReadinessPhase, ...]

    @property
    def failed_phases(self) -> tuple[str, ...]:
        return tuple(phase.name for phase in self.phases if not phase.ready)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "phase_order": list(DEPLOYMENT_READINESS_PHASE_ORDER),
            "failed_phases": list(self.failed_phases),
            "phases": [phase.as_dict() for phase in self.phases],
        }


def evaluate_deployment_readiness(evidence: Mapping[str, Any]) -> DeploymentReadinessReport:
    phases = (
        _phase_37_vercel(_mapping(evidence.get("vercel_project"))),
        _phase_38_environment(_mapping(evidence.get("environment_contract"))),
        _phase_39_database(_mapping(evidence.get("database_release"))),
        _phase_40_artifacts(_mapping(evidence.get("build_artifacts"))),
        _phase_41_auth_traffic(_mapping(evidence.get("auth_traffic"))),
        _phase_42_runbook(_mapping(evidence.get("final_runbook"))),
    )
    return DeploymentReadinessReport(ready=all(phase.ready for phase in phases), phases=phases)


def required_deployment_readiness_contract() -> dict[str, object]:
    return {
        "phase_order": list(DEPLOYMENT_READINESS_PHASE_ORDER),
        "requires": {
            "vercel_project": [
                "team_id",
                "project_id",
                "project_name",
                "git_repository",
                "production_branch",
                "root_directory",
                "framework",
                "preview_deployments_enabled",
            ],
            "environment_contract": [
                "vercel_token_configured",
                "vercel_org_id_configured",
                "vercel_project_id_configured",
                "database_url_configured",
                "supabase_url_configured",
                "supabase_publishable_key_configured",
                "api_base_url_configured",
                "web_app_url_configured",
                "cors_origins_https_only",
                "no_plaintext_credentials_in_repo",
            ],
            "database_release": [
                "supabase_project_healthy",
                "migrations_applied_through",
                "latest_required_migration",
                "rls_no_policy_count",
                "security_warn_count",
                "managed_platform_warnings_accepted",
            ],
            "build_artifacts": [
                "github_ci_success",
                "api_ruff_passed",
                "api_typecheck_passed",
                "api_tests_passed",
                "web_build_passed",
                "production_compose_valid",
                "dockerfile_valid",
                "deployment_commit_sha",
            ],
            "auth_traffic": [
                "supabase_auth_site_url_configured",
                "redirect_urls_configured",
                "owner_test_user_ready",
                "other_test_user_ready",
                "smoke_user_ready",
                "rate_limits_configured",
                "deployment_smoke_plan_ready",
            ],
            "final_runbook": [
                "phase_30_gate_plan_ready",
                "phase_31_36_gate_plan_ready",
                "rollback_plan_ready",
                "dns_cutover_plan_ready",
                "commercial_approval_plan_ready",
                "launch_owner_named",
            ],
        },
        "policy": "missing_or_placeholder_evidence_fails_closed",
    }


def _phase_37_vercel(data: Mapping[str, Any]) -> DeploymentReadinessPhase:
    errors: list[str] = []
    if not _text(data.get("team_id")).startswith("team_"):
        errors.append("vercel_team_id_missing_or_invalid")
    if not _text(data.get("project_id")).startswith("prj_"):
        errors.append("vercel_project_id_missing_or_invalid")
    if not _text(data.get("project_name")):
        errors.append("vercel_project_name_missing")
    if "/" not in _text(data.get("git_repository")):
        errors.append("git_repository_missing_or_invalid")
    if _text(data.get("production_branch")) not in {"main", "master"}:
        errors.append("production_branch_must_be_main_or_master")
    if _text(data.get("root_directory")) not in {".", "apps/web"}:
        errors.append("root_directory_not_recognized")
    if not _text(data.get("framework")):
        errors.append("framework_missing")
    if not _true(data.get("preview_deployments_enabled")):
        errors.append("preview_deployments_not_enabled")
    return _phase("phase_37_vercel_project_linkage", errors, data)


def _phase_38_environment(data: Mapping[str, Any]) -> DeploymentReadinessPhase:
    errors: list[str] = []
    for key in (
        "vercel_token_configured",
        "vercel_org_id_configured",
        "vercel_project_id_configured",
        "database_url_configured",
        "supabase_url_configured",
        "supabase_publishable_key_configured",
        "api_base_url_configured",
        "web_app_url_configured",
        "cors_origins_https_only",
        "no_plaintext_credentials_in_repo",
    ):
        if not _true(data.get(key)):
            errors.append(f"{key}_missing_or_false")
    return _phase("phase_38_environment_contract", errors, data)


def _phase_39_database(data: Mapping[str, Any]) -> DeploymentReadinessPhase:
    errors: list[str] = []
    if not _true(data.get("supabase_project_healthy")):
        errors.append("supabase_project_not_healthy")
    if _text(data.get("latest_required_migration")) != LATEST_REQUIRED_MIGRATION:
        errors.append("latest_required_migration_mismatch")
    if _text(data.get("migrations_applied_through")) != LATEST_REQUIRED_MIGRATION:
        errors.append("required_migration_not_applied")
    if _number(data.get("rls_no_policy_count"), default=1) != 0:
        errors.append("rls_no_policy_findings_present")
    warn_count = _number(data.get("security_warn_count"), default=1)
    if warn_count != 0 and not _true(data.get("managed_platform_warnings_accepted")):
        errors.append("unaccepted_security_warnings_present")
    return _phase("phase_39_database_migration_readiness", errors, data)


def _phase_40_artifacts(data: Mapping[str, Any]) -> DeploymentReadinessPhase:
    errors: list[str] = []
    for key in (
        "github_ci_success",
        "api_ruff_passed",
        "api_typecheck_passed",
        "api_tests_passed",
        "web_build_passed",
        "production_compose_valid",
        "dockerfile_valid",
    ):
        if not _true(data.get(key)):
            errors.append(f"{key}_missing_or_false")
    if len(_text(data.get("deployment_commit_sha"))) < 7:
        errors.append("deployment_commit_sha_missing")
    return _phase("phase_40_build_artifact_readiness", errors, data)


def _phase_41_auth_traffic(data: Mapping[str, Any]) -> DeploymentReadinessPhase:
    errors: list[str] = []
    for key in (
        "supabase_auth_site_url_configured",
        "redirect_urls_configured",
        "owner_test_user_ready",
        "other_test_user_ready",
        "smoke_user_ready",
        "rate_limits_configured",
        "deployment_smoke_plan_ready",
    ):
        if not _true(data.get(key)):
            errors.append(f"{key}_missing_or_false")
    return _phase("phase_41_auth_traffic_readiness", errors, data)


def _phase_42_runbook(data: Mapping[str, Any]) -> DeploymentReadinessPhase:
    errors: list[str] = []
    for key in (
        "phase_30_gate_plan_ready",
        "phase_31_36_gate_plan_ready",
        "rollback_plan_ready",
        "dns_cutover_plan_ready",
        "commercial_approval_plan_ready",
        "launch_owner_named",
    ):
        if not _true(data.get(key)):
            errors.append(f"{key}_missing_or_false")
    return _phase("phase_42_final_deployment_runbook", errors, data)


def _phase(name: str, errors: list[str], diagnostics: Mapping[str, Any]) -> DeploymentReadinessPhase:
    return DeploymentReadinessPhase(
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
