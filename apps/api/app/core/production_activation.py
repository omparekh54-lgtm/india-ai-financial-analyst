from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PRODUCTION_ACTIVATION_PHASE_ORDER = (
    "phase_43_vercel_live_project",
    "phase_44_production_environment",
    "phase_45_supabase_real_data_readiness",
    "phase_46_supabase_security_finalization",
)

MIN_REAL_SECURITIES = 1000
MIN_REPRESENTATIVE_RESEARCH_JOBS = 5
LATEST_REQUIRED_MIGRATION = "0026_backend_only_rls_deny_policies.sql"
EXPECTED_REPOSITORY = "omparekh54-lgtm/india-ai-financial-analyst"


@dataclass(frozen=True)
class ProductionActivationPhase:
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
class ProductionActivationReport:
    ready: bool
    phases: tuple[ProductionActivationPhase, ...]

    @property
    def failed_phases(self) -> tuple[str, ...]:
        return tuple(phase.name for phase in self.phases if not phase.ready)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "phase_order": list(PRODUCTION_ACTIVATION_PHASE_ORDER),
            "failed_phases": list(self.failed_phases),
            "phases": [phase.as_dict() for phase in self.phases],
        }


def evaluate_production_activation(evidence: Mapping[str, Any]) -> ProductionActivationReport:
    phases = (
        _phase_43_vercel_live_project(_mapping(evidence.get("vercel_live_project"))),
        _phase_44_production_environment(_mapping(evidence.get("production_environment"))),
        _phase_45_supabase_real_data(_mapping(evidence.get("supabase_real_data"))),
        _phase_46_supabase_security(_mapping(evidence.get("supabase_security"))),
    )
    return ProductionActivationReport(
        ready=all(phase.ready for phase in phases),
        phases=phases,
    )


def required_production_activation_contract() -> dict[str, object]:
    return {
        "phase_order": list(PRODUCTION_ACTIVATION_PHASE_ORDER),
        "requires": {
            "vercel_live_project": [
                "team_id",
                "project_id",
                "project_name",
                "git_repository",
                "production_branch",
                "root_directory",
                "framework",
                "preview_deployment_url",
                "preview_deployment_ready",
                "production_domains_https_ready",
            ],
            "production_environment": [
                "vercel_env_pulled",
                "vercel_org_id_configured",
                "vercel_project_id_configured",
                "database_url_configured",
                "supabase_url_configured",
                "supabase_publishable_key_configured",
                "api_base_url_configured",
                "web_app_url_configured",
                "cors_origins_https_only",
                "frontend_uses_publishable_key_only",
                "service_role_absent_from_frontend",
                "no_plaintext_credentials_in_repo",
            ],
            "supabase_real_data": [
                "securities_count",
                "market_bars_count",
                "evidence_chunks_count",
                "research_jobs_count",
                "research_reports_count",
                "representative_research_jobs_count",
                "agent_15_completed_count",
                "agent_16_completed_count",
                "non_production_source_rows",
                "production_corpus_gate_ready",
            ],
            "supabase_security": [
                "project_status",
                "database_postgres_version",
                "migrations_applied_through",
                "latest_required_migration",
                "rls_no_policy_count",
                "security_error_count",
                "security_warn_count",
                "managed_platform_warnings_accepted",
                "pg_net_public_exception_status",
                "performance_blocker_count",
            ],
        },
        "policy": "missing_or_placeholder_activation_evidence_fails_closed",
    }


def _phase_43_vercel_live_project(data: Mapping[str, Any]) -> ProductionActivationPhase:
    errors: list[str] = []
    if not _text(data.get("team_id")).startswith("team_"):
        errors.append("vercel_team_id_missing_or_invalid")
    if not _text(data.get("project_id")).startswith("prj_"):
        errors.append("vercel_project_id_missing_or_invalid")
    if not _text(data.get("project_name")):
        errors.append("vercel_project_name_missing")
    if _text(data.get("git_repository")) != EXPECTED_REPOSITORY:
        errors.append("vercel_project_not_linked_to_expected_repository")
    if _text(data.get("production_branch")) not in {"main", "master"}:
        errors.append("production_branch_must_be_main_or_master")
    if _text(data.get("root_directory")) not in {".", "apps/web"}:
        errors.append("root_directory_not_recognized")
    if _text(data.get("framework")).lower() not in {"nextjs", "next.js"}:
        errors.append("framework_must_be_nextjs")
    if not _text(data.get("preview_deployment_url")).startswith("https://"):
        errors.append("preview_deployment_url_must_be_https")
    if not _true(data.get("preview_deployment_ready")):
        errors.append("preview_deployment_not_ready")
    if not _true(data.get("production_domains_https_ready")):
        errors.append("production_domains_https_not_ready")
    return _phase("phase_43_vercel_live_project", errors, data)


def _phase_44_production_environment(data: Mapping[str, Any]) -> ProductionActivationPhase:
    errors: list[str] = []
    for key in (
        "vercel_env_pulled",
        "vercel_org_id_configured",
        "vercel_project_id_configured",
        "database_url_configured",
        "supabase_url_configured",
        "supabase_publishable_key_configured",
        "api_base_url_configured",
        "web_app_url_configured",
        "cors_origins_https_only",
        "frontend_uses_publishable_key_only",
        "service_role_absent_from_frontend",
        "no_plaintext_credentials_in_repo",
    ):
        if not _true(data.get(key)):
            errors.append(f"{key}_missing_or_false")
    return _phase("phase_44_production_environment", errors, data)


def _phase_45_supabase_real_data(data: Mapping[str, Any]) -> ProductionActivationPhase:
    errors: list[str] = []
    if _number(data.get("securities_count"), default=0) < MIN_REAL_SECURITIES:
        errors.append("minimum_real_security_count_not_met")
    if _number(data.get("market_bars_count"), default=0) <= 0:
        errors.append("market_bars_missing")
    if _number(data.get("evidence_chunks_count"), default=0) <= 0:
        errors.append("evidence_chunks_missing")
    if _number(data.get("research_jobs_count"), default=0) <= 0:
        errors.append("research_jobs_missing")
    if _number(data.get("research_reports_count"), default=0) <= 0:
        errors.append("research_reports_missing")
    if (
        _number(data.get("representative_research_jobs_count"), default=0)
        < MIN_REPRESENTATIVE_RESEARCH_JOBS
    ):
        errors.append("representative_research_jobs_below_minimum")
    if (
        _number(data.get("agent_15_completed_count"), default=0)
        < MIN_REPRESENTATIVE_RESEARCH_JOBS
    ):
        errors.append("agent_15_acceptance_runs_below_minimum")
    if (
        _number(data.get("agent_16_completed_count"), default=0)
        < MIN_REPRESENTATIVE_RESEARCH_JOBS
    ):
        errors.append("agent_16_acceptance_runs_below_minimum")
    if _number(data.get("non_production_source_rows"), default=1) != 0:
        errors.append("non_production_source_rows_present")
    if not _true(data.get("production_corpus_gate_ready")):
        errors.append("production_corpus_gate_not_ready")
    return _phase("phase_45_supabase_real_data_readiness", errors, data)


def _phase_46_supabase_security(data: Mapping[str, Any]) -> ProductionActivationPhase:
    errors: list[str] = []
    if _text(data.get("project_status")) != "ACTIVE_HEALTHY":
        errors.append("supabase_project_not_active_healthy")
    if not _text(data.get("database_postgres_version")):
        errors.append("database_postgres_version_missing")
    if _text(data.get("migrations_applied_through")) != LATEST_REQUIRED_MIGRATION:
        errors.append("required_migration_not_applied")
    if _text(data.get("latest_required_migration")) != LATEST_REQUIRED_MIGRATION:
        errors.append("latest_required_migration_mismatch")
    if _number(data.get("rls_no_policy_count"), default=1) != 0:
        errors.append("rls_no_policy_findings_present")
    if _number(data.get("security_error_count"), default=1) != 0:
        errors.append("security_errors_present")
    security_warn_count = _number(data.get("security_warn_count"), default=1)
    if security_warn_count != 0 and not _true(data.get("managed_platform_warnings_accepted")):
        errors.append("unaccepted_security_warnings_present")
    pg_net_status = _text(data.get("pg_net_public_exception_status")).lower()
    if security_warn_count != 0 and pg_net_status not in {"accepted", "not_applicable"}:
        errors.append("pg_net_public_exception_not_accepted")
    if _number(data.get("performance_blocker_count"), default=1) != 0:
        errors.append("performance_blockers_present")
    return _phase("phase_46_supabase_security_finalization", errors, data)


def _phase(
    name: str,
    errors: list[str],
    diagnostics: Mapping[str, Any],
) -> ProductionActivationPhase:
    return ProductionActivationPhase(
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
