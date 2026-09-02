from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

POST_LAUNCH_PHASE_ORDER = (
    "phase_31_observability",
    "phase_32_data_freshness",
    "phase_33_research_quality",
    "phase_34_security_acceptance",
    "phase_35_cost_quota_controls",
    "phase_36_rollback_incident_readiness",
)


@dataclass(frozen=True)
class PhaseAcceptance:
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
class PostLaunchAcceptanceReport:
    ready: bool
    phases: tuple[PhaseAcceptance, ...]

    @property
    def failed_phases(self) -> tuple[str, ...]:
        return tuple(phase.name for phase in self.phases if not phase.ready)

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "phase_order": list(POST_LAUNCH_PHASE_ORDER),
            "failed_phases": list(self.failed_phases),
            "phases": [phase.as_dict() for phase in self.phases],
        }


def evaluate_post_launch_evidence(evidence: Mapping[str, Any]) -> PostLaunchAcceptanceReport:
    """Evaluate Phases 31-36 from externally collected production evidence.

    The gate intentionally accepts evidence as input. It does not infer production
    readiness from CI success, local tests, placeholder rows, or missing fields.
    """

    phases = (
        _phase_31_observability(_mapping(evidence.get("observability"))),
        _phase_32_data_freshness(_mapping(evidence.get("data_freshness"))),
        _phase_33_research_quality(_mapping(evidence.get("research_quality"))),
        _phase_34_security(_mapping(evidence.get("security"))),
        _phase_35_cost_quota(_mapping(evidence.get("cost_quota"))),
        _phase_36_rollback_incident(_mapping(evidence.get("rollback_incident"))),
    )
    return PostLaunchAcceptanceReport(
        ready=all(phase.ready for phase in phases),
        phases=phases,
    )


def required_post_launch_evidence_contract() -> dict[str, object]:
    return {
        "phase_order": list(POST_LAUNCH_PHASE_ORDER),
        "requires": {
            "observability": [
                "production_deployment_url",
                "vercel_project_id",
                "error_monitoring_configured",
                "privacy_filtering_enabled",
                "alert_routes",
                "critical_runtime_error_count_24h",
            ],
            "data_freshness": [
                "corpus_ready",
                "stale_market_data_count",
                "failed_ingestion_runs_24h",
                "official_feed_lag_minutes",
                "macro_series_present",
                "benchmark_codes_with_bars",
            ],
            "research_quality": [
                "evaluated_reports",
                "distinct_real_sectors",
                "validated_claim_coverage",
                "unsupported_claim_rate",
                "validator_completed",
                "calibration_errors_open",
            ],
            "security": [
                "supabase_security_warn_count",
                "rls_enabled_no_policy_count",
                "critical_dependency_vulnerabilities",
                "exposed_secret_findings",
                "auth_isolation_passed",
                "security_review_approved",
            ],
            "cost_quota": [
                "free_only",
                "paid_fallback_enabled",
                "monthly_budget_configured",
                "usage_caps_configured",
                "provider_quota_alerts_configured",
                "unapproved_paid_spend",
            ],
            "rollback_incident": [
                "production_backup_verified",
                "rollback_target_deployment_id",
                "restore_drill_passed",
                "incident_runbook_url",
                "on_call_route_configured",
                "release_owner_approved",
            ],
        },
        "policy": "missing_or_placeholder_evidence_fails_closed",
    }


def _phase_31_observability(data: Mapping[str, Any]) -> PhaseAcceptance:
    errors: list[str] = []
    deployment_url = _text(data.get("production_deployment_url"))
    project_id = _text(data.get("vercel_project_id"))
    alert_routes = _list(data.get("alert_routes"))

    if not deployment_url.startswith("https://"):
        errors.append("production_deployment_url_must_be_https")
    if not project_id.startswith("prj_"):
        errors.append("vercel_project_id_missing_or_invalid")
    if not _true(data.get("error_monitoring_configured")):
        errors.append("error_monitoring_not_configured")
    if not _true(data.get("privacy_filtering_enabled")):
        errors.append("privacy_filtering_not_enabled")
    if not alert_routes:
        errors.append("alert_routes_missing")
    if _number(data.get("critical_runtime_error_count_24h"), default=1) > 0:
        errors.append("critical_runtime_errors_present")

    return _phase("phase_31_observability", errors, data)


def _phase_32_data_freshness(data: Mapping[str, Any]) -> PhaseAcceptance:
    errors: list[str] = []
    if not _true(data.get("corpus_ready")):
        errors.append("corpus_not_ready")
    if _number(data.get("stale_market_data_count"), default=1) != 0:
        errors.append("stale_market_data_present")
    if _number(data.get("failed_ingestion_runs_24h"), default=1) != 0:
        errors.append("failed_ingestion_runs_present")
    if _number(data.get("official_feed_lag_minutes"), default=10_000) > 120:
        errors.append("official_feed_lag_exceeds_120_minutes")
    if _number(data.get("macro_series_present"), default=0) < 9:
        errors.append("required_macro_series_missing")
    if _number(data.get("benchmark_codes_with_bars"), default=0) < 2:
        errors.append("required_benchmark_history_missing")

    return _phase("phase_32_data_freshness", errors, data)


def _phase_33_research_quality(data: Mapping[str, Any]) -> PhaseAcceptance:
    errors: list[str] = []
    if _number(data.get("evaluated_reports"), default=0) < 25:
        errors.append("insufficient_evaluated_reports")
    if _number(data.get("distinct_real_sectors"), default=0) < 4:
        errors.append("insufficient_sector_coverage")
    if _number(data.get("validated_claim_coverage"), default=0.0) < 0.90:
        errors.append("validated_claim_coverage_below_90_percent")
    if _number(data.get("unsupported_claim_rate"), default=1.0) > 0.02:
        errors.append("unsupported_claim_rate_above_2_percent")
    if not _true(data.get("validator_completed")):
        errors.append("validator_completion_missing")
    if _number(data.get("calibration_errors_open"), default=1) != 0:
        errors.append("open_calibration_errors_present")

    return _phase("phase_33_research_quality", errors, data)


def _phase_34_security(data: Mapping[str, Any]) -> PhaseAcceptance:
    errors: list[str] = []
    if _number(data.get("supabase_security_warn_count"), default=1) != 0:
        errors.append("supabase_security_warnings_present")
    if _number(data.get("rls_enabled_no_policy_count"), default=1) != 0:
        errors.append("rls_enabled_tables_without_policies")
    if _number(data.get("critical_dependency_vulnerabilities"), default=1) != 0:
        errors.append("critical_dependency_vulnerabilities_present")
    if _number(data.get("exposed_secret_findings"), default=1) != 0:
        errors.append("exposed_secret_findings_present")
    if not _true(data.get("auth_isolation_passed")):
        errors.append("auth_isolation_not_passed")
    if not _true(data.get("security_review_approved")):
        errors.append("security_review_approval_missing")

    return _phase("phase_34_security_acceptance", errors, data)


def _phase_35_cost_quota(data: Mapping[str, Any]) -> PhaseAcceptance:
    errors: list[str] = []
    if not _true(data.get("free_only")):
        errors.append("free_only_policy_not_enabled")
    if _true(data.get("paid_fallback_enabled")):
        errors.append("paid_fallback_enabled")
    if not _true(data.get("monthly_budget_configured")):
        errors.append("monthly_budget_missing")
    if not _true(data.get("usage_caps_configured")):
        errors.append("usage_caps_missing")
    if not _true(data.get("provider_quota_alerts_configured")):
        errors.append("provider_quota_alerts_missing")
    if _number(data.get("unapproved_paid_spend"), default=1) != 0:
        errors.append("unapproved_paid_spend_present")

    return _phase("phase_35_cost_quota_controls", errors, data)


def _phase_36_rollback_incident(data: Mapping[str, Any]) -> PhaseAcceptance:
    errors: list[str] = []
    if not _true(data.get("production_backup_verified")):
        errors.append("production_backup_not_verified")
    if not _text(data.get("rollback_target_deployment_id")):
        errors.append("rollback_target_deployment_missing")
    if not _true(data.get("restore_drill_passed")):
        errors.append("restore_drill_not_passed")
    if not _text(data.get("incident_runbook_url")).startswith("https://"):
        errors.append("incident_runbook_url_missing_or_not_https")
    if not _true(data.get("on_call_route_configured")):
        errors.append("on_call_route_missing")
    if not _true(data.get("release_owner_approved")):
        errors.append("release_owner_approval_missing")

    return _phase("phase_36_rollback_incident_readiness", errors, data)


def _phase(name: str, errors: list[str], diagnostics: Mapping[str, Any]) -> PhaseAcceptance:
    return PhaseAcceptance(
        name=name,
        ready=not errors,
        errors=tuple(errors),
        diagnostics=dict(diagnostics),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


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
