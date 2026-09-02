from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PRODUCTION_PROMOTION_PHASE_ORDER = (
    "phase_51_production_branch_promotion",
    "phase_52_vercel_production_domain",
    "phase_53_backend_post_promotion_sync",
    "phase_54_launch_decision_register",
)

EXPECTED_REPOSITORY = "omparekh54-lgtm/india-ai-financial-analyst"
EXPECTED_API_BASE_URL = "https://api-production-d331d.up.railway.app"
EXPECTED_WEB_ROOT = "apps/web"
EXPECTED_PRODUCTION_URL = "https://india-ai-financial-analyst.vercel.app"
MIN_READY_HTTP_STATUS = 200
MIN_REQUIRED_WORKERS = 3


@dataclass(frozen=True)
class ProductionPromotionPhase:
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
class ProductionPromotionReport:
    ready: bool
    phases: tuple[ProductionPromotionPhase, ...]

    @property
    def failed_phases(self) -> tuple[str, ...]:
        return tuple(phase.name for phase in self.phases if not phase.ready)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "phase_order": list(PRODUCTION_PROMOTION_PHASE_ORDER),
            "failed_phases": list(self.failed_phases),
            "phases": [phase.as_dict() for phase in self.phases],
        }


def evaluate_production_promotion(evidence: Mapping[str, Any]) -> ProductionPromotionReport:
    phases = (
        _phase_51_production_branch_promotion(
            _mapping(evidence.get("production_branch_promotion"))
        ),
        _phase_52_vercel_production_domain(_mapping(evidence.get("vercel_production_domain"))),
        _phase_53_backend_post_promotion_sync(
            _mapping(evidence.get("backend_post_promotion_sync"))
        ),
        _phase_54_launch_decision_register(_mapping(evidence.get("launch_decision_register"))),
    )
    return ProductionPromotionReport(
        ready=all(phase.ready for phase in phases),
        phases=phases,
    )


def required_production_promotion_contract() -> dict[str, object]:
    return {
        "phase_order": list(PRODUCTION_PROMOTION_PHASE_ORDER),
        "requires": {
            "production_branch_promotion": [
                "pull_request_number",
                "git_repository",
                "source_branch",
                "target_branch",
                "expected_head_sha",
                "required_checks_green",
                "review_or_owner_approval_recorded",
                "protected_environment_approval_recorded",
                "merge_strategy",
                "deployment_cutover_gate_ready",
                "no_uncommitted_config_drift",
            ],
            "vercel_production_domain": [
                "production_deployment_id",
                "production_deployment_ready",
                "production_commit_sha",
                "promoted_commit_sha",
                "production_url",
                "production_url_https",
                "production_alias_ready",
                "root_directory",
                "framework",
                "env_file_detected",
                "api_base_url",
                "runtime_error_count",
            ],
            "backend_post_promotion_sync": [
                "railway_project_id",
                "railway_environment_id",
                "railway_api_deployment_status",
                "railway_worker_success_count",
                "railway_branch_strategy",
                "railway_source_branch_matches_promoted",
                "api_health_status",
                "api_ready_status",
                "api_ready_database_healthy",
                "cors_includes_production_url",
                "runtime_blocker_count",
                "free_only_enabled",
                "commercial_launch_disabled",
            ],
            "launch_decision_register": [
                "launch_decision",
                "known_blockers_recorded",
                "corpus_gate_status",
                "supabase_security_exception_status",
                "sentry_monitoring_status",
                "npm_audit_status",
                "rollback_reference",
                "monitoring_reference",
                "operator_signoff_reference",
                "conditional_approval_reference",
            ],
        },
        "policy": "missing_or_placeholder_production_promotion_evidence_fails_closed",
    }


def _phase_51_production_branch_promotion(data: Mapping[str, Any]) -> ProductionPromotionPhase:
    errors: list[str] = []
    if _number(data.get("pull_request_number"), default=0) < 1:
        errors.append("pull_request_number_missing")
    if _text(data.get("git_repository")) != EXPECTED_REPOSITORY:
        errors.append("git_repository_not_expected_project")
    if _text(data.get("source_branch")) == _text(data.get("target_branch")):
        errors.append("source_and_target_branch_must_differ")
    if _text(data.get("target_branch")) not in {"main", "master"}:
        errors.append("target_branch_must_be_main_or_master")
    if not _text(data.get("expected_head_sha")):
        errors.append("expected_head_sha_missing")
    if _text(data.get("merge_strategy")).lower() not in {"merge", "squash", "rebase", "manual"}:
        errors.append("merge_strategy_missing_or_invalid")
    for key in (
        "required_checks_green",
        "review_or_owner_approval_recorded",
        "protected_environment_approval_recorded",
        "deployment_cutover_gate_ready",
        "no_uncommitted_config_drift",
    ):
        if not _true(data.get(key)):
            errors.append(f"{key}_missing_or_false")
    return _phase("phase_51_production_branch_promotion", errors, data)


def _phase_52_vercel_production_domain(data: Mapping[str, Any]) -> ProductionPromotionPhase:
    errors: list[str] = []
    if not _text(data.get("production_deployment_id")).startswith("dpl_"):
        errors.append("production_deployment_id_missing_or_invalid")
    if not _true(data.get("production_deployment_ready")):
        errors.append("production_deployment_not_ready")

    production_commit_sha = _text(data.get("production_commit_sha"))
    promoted_commit_sha = _text(data.get("promoted_commit_sha"))
    if not production_commit_sha:
        errors.append("production_commit_sha_missing")
    if not promoted_commit_sha:
        errors.append("promoted_commit_sha_missing")
    if production_commit_sha and promoted_commit_sha and production_commit_sha != promoted_commit_sha:
        errors.append("production_commit_sha_does_not_match_promoted_commit")

    production_url = _text(data.get("production_url"))
    if not production_url.startswith("https://"):
        errors.append("production_url_missing_or_not_https")
    if production_url and production_url != EXPECTED_PRODUCTION_URL:
        errors.append("production_url_not_expected_domain")
    if not _true(data.get("production_url_https")):
        errors.append("production_url_https_not_verified")
    if not _true(data.get("production_alias_ready")):
        errors.append("production_alias_not_ready")
    if _text(data.get("root_directory")) != EXPECTED_WEB_ROOT:
        errors.append("vercel_root_directory_not_apps_web")
    if _text(data.get("framework")).lower().replace(".", "") != "nextjs":
        errors.append("vercel_framework_not_nextjs")
    if not _true(data.get("env_file_detected")):
        errors.append("vercel_env_production_file_not_detected")
    if _text(data.get("api_base_url")) != EXPECTED_API_BASE_URL:
        errors.append("vercel_api_base_url_not_expected_railway_url")
    if _number(data.get("runtime_error_count"), default=1) != 0:
        errors.append("vercel_runtime_errors_present")
    return _phase("phase_52_vercel_production_domain", errors, data)


def _phase_53_backend_post_promotion_sync(data: Mapping[str, Any]) -> ProductionPromotionPhase:
    errors: list[str] = []
    if not _text(data.get("railway_project_id")):
        errors.append("railway_project_id_missing")
    if not _text(data.get("railway_environment_id")):
        errors.append("railway_environment_id_missing")
    if _text(data.get("railway_api_deployment_status")).upper() != "SUCCESS":
        errors.append("railway_api_deployment_not_success")
    if _number(data.get("railway_worker_success_count"), default=0) < MIN_REQUIRED_WORKERS:
        errors.append("railway_worker_services_not_success")
    branch_strategy = _text(data.get("railway_branch_strategy")).lower()
    if branch_strategy not in {
        "main_after_merge",
        "feature_branch_until_final_merge",
        "manual_pin_accepted",
    }:
        errors.append("railway_branch_strategy_missing_or_invalid")
    if branch_strategy == "main_after_merge" and not _true(
        data.get("railway_source_branch_matches_promoted")
    ):
        errors.append("railway_source_branch_not_promoted_branch")
    if _number(data.get("api_health_status"), default=0) != MIN_READY_HTTP_STATUS:
        errors.append("api_health_check_not_200")
    if _number(data.get("api_ready_status"), default=0) != MIN_READY_HTTP_STATUS:
        errors.append("api_ready_check_not_200")
    if not _true(data.get("api_ready_database_healthy")):
        errors.append("api_ready_database_not_healthy")
    if not _true(data.get("cors_includes_production_url")):
        errors.append("cors_does_not_include_production_url")
    if _number(data.get("runtime_blocker_count"), default=1) != 0:
        errors.append("backend_runtime_blockers_present")
    if not _true(data.get("free_only_enabled")):
        errors.append("free_only_not_enabled")
    if not _true(data.get("commercial_launch_disabled")):
        errors.append("commercial_launch_not_disabled")
    return _phase("phase_53_backend_post_promotion_sync", errors, data)


def _phase_54_launch_decision_register(data: Mapping[str, Any]) -> ProductionPromotionPhase:
    errors: list[str] = []
    launch_decision = _text(data.get("launch_decision")).lower()
    if launch_decision not in {"go", "conditional_go", "blocked"}:
        errors.append("launch_decision_missing_or_invalid")
    if not _true(data.get("known_blockers_recorded")):
        errors.append("known_blockers_not_recorded")

    corpus_gate_status = _text(data.get("corpus_gate_status")).lower()
    security_status = _text(data.get("supabase_security_exception_status")).lower()
    sentry_status = _text(data.get("sentry_monitoring_status")).lower()
    npm_status = _text(data.get("npm_audit_status")).lower()
    if corpus_gate_status not in {"passed", "blocked_by_real_data", "not_ready"}:
        errors.append("corpus_gate_status_missing_or_invalid")
    if security_status not in {"accepted", "not_applicable", "pending", "blocked"}:
        errors.append("supabase_security_exception_status_missing_or_invalid")
    if sentry_status not in {"configured", "accepted_deferred", "blocked"}:
        errors.append("sentry_monitoring_status_missing_or_invalid")
    if npm_status not in {"clean", "accepted_deferred", "blocked"}:
        errors.append("npm_audit_status_missing_or_invalid")

    for key in ("rollback_reference", "monitoring_reference", "operator_signoff_reference"):
        if not _text(data.get(key)):
            errors.append(f"{key}_missing")

    if launch_decision == "go":
        if corpus_gate_status != "passed":
            errors.append("go_decision_requires_passed_corpus_gate")
        if security_status not in {"accepted", "not_applicable"}:
            errors.append("go_decision_requires_resolved_supabase_security_status")
        if sentry_status != "configured":
            errors.append("go_decision_requires_configured_sentry_monitoring")
        if npm_status != "clean":
            errors.append("go_decision_requires_clean_npm_audit")
    if launch_decision == "conditional_go" and not _text(
        data.get("conditional_approval_reference")
    ):
        errors.append("conditional_go_requires_approval_reference")
    if launch_decision == "blocked" and not _true(data.get("known_blockers_recorded")):
        errors.append("blocked_decision_requires_recorded_blockers")
    return _phase("phase_54_launch_decision_register", errors, data)


def _phase(
    name: str,
    errors: list[str],
    diagnostics: Mapping[str, Any],
) -> ProductionPromotionPhase:
    return ProductionPromotionPhase(
        name=name,
        ready=not errors,
        errors=tuple(errors),
        diagnostics=dict(diagnostics),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False
