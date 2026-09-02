from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet

from app.core.config import Settings

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    severity: Severity
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True)
class ReadinessReport:
    issues: tuple[ReadinessIssue, ...]

    @property
    def errors(self) -> tuple[ReadinessIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ReadinessIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def ready(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "errors": [issue.as_dict() for issue in self.errors],
            "warnings": [issue.as_dict() for issue in self.warnings],
        }


def audit_settings(settings: Settings) -> ReadinessReport:
    issues: list[ReadinessIssue] = []
    production = settings.app_env.strip().lower() == "production"

    def add(code: str, severity: Severity, message: str) -> None:
        issues.append(ReadinessIssue(code=code, severity=severity, message=message))

    if production:
        if not settings.database_url:
            add("database_missing", "error", "DATABASE_URL is required in production.")
        if not settings.supabase_url or not settings.supabase_publishable_key:
            add(
                "auth_missing",
                "error",
                "SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required in production.",
            )
        if not _is_https(settings.web_app_url):
            add("web_app_https", "error", "WEB_APP_URL must use HTTPS in production.")
        for origin in settings.cors_origin_list:
            if not _is_https(origin):
                add(
                    "cors_https",
                    "error",
                    f"Production CORS origin must use HTTPS: {origin}",
                )
        if not settings.sentry_dsn:
            add(
                "sentry_missing",
                "warning",
                "SENTRY_DSN is not configured; production errors will not be reported to Sentry.",
            )

    llm_keys = (
        settings.groq_api_key,
        settings.gemini_api_key,
        settings.nvidia_api_key,
        settings.cerebras_api_key,
    )
    if settings.enable_external_llm_calls and not any(llm_keys):
        add(
            "llm_provider_missing",
            "error",
            "ENABLE_EXTERNAL_LLM_CALLS is true but no LLM provider key is configured.",
        )

    if settings.enable_multimodal_document_analysis and (
        not settings.enable_external_llm_calls or not settings.gemini_api_key
    ):
        add(
            "multimodal_gemini_missing",
            "error",
            "Multimodal filing analysis requires external LLM calls and GEMINI_API_KEY.",
        )

    if settings.enable_audio_transcription and (
        not settings.enable_external_llm_calls or not settings.gemini_api_key
    ):
        add(
            "audio_gemini_missing",
            "error",
            "Audio transcription requires external LLM calls and GEMINI_API_KEY.",
        )

    if settings.enable_semantic_retrieval and importlib.util.find_spec("sentence_transformers") is None:
        add(
            "embedding_runtime_missing",
            "error",
            "Semantic retrieval is enabled but sentence-transformers is not installed.",
        )

    if settings.enable_live_market:
        required = {
            "UPSTOX_CLIENT_ID": settings.upstox_client_id,
            "UPSTOX_CLIENT_SECRET": settings.upstox_client_secret,
            "UPSTOX_REDIRECT_URI": settings.upstox_redirect_uri,
            "BROKER_TOKEN_ENCRYPTION_KEY": settings.broker_token_encryption_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            add(
                "live_market_credentials_missing",
                "error",
                "Live market is enabled but required Upstox/broker secrets are missing: "
                + ", ".join(missing),
            )
        elif not _valid_fernet_key(settings.broker_token_encryption_key):
            add(
                "broker_encryption_invalid",
                "error",
                "BROKER_TOKEN_ENCRYPTION_KEY is not a valid Fernet key.",
            )
        if production and settings.upstox_redirect_uri and not _is_https(settings.upstox_redirect_uri):
            add(
                "upstox_redirect_https",
                "error",
                "UPSTOX_REDIRECT_URI must use HTTPS when live market is enabled in production.",
            )

    if settings.enable_external_data_calls and not any(
        (settings.tavily_api_key, settings.fred_api_key, settings.alpha_vantage_api_key)
    ):
        add(
            "external_data_keys_missing",
            "warning",
            "External data calls are enabled but Tavily/FRED/Alpha Vantage keys are all absent; "
            "only keyless/official-source adapters can run.",
        )

    if settings.enable_product_telemetry and not settings.posthog_key:
        add(
            "posthog_key_missing",
            "error",
            "ENABLE_PRODUCT_TELEMETRY is true but POSTHOG_KEY is not configured.",
        )

    return ReadinessReport(tuple(issues))


def assert_production_ready(settings: Settings) -> ReadinessReport:
    report = audit_settings(settings)
    if settings.app_env.strip().lower() == "production" and not report.ready:
        codes = ", ".join(issue.code for issue in report.errors)
        raise RuntimeError(f"Unsafe production configuration: {codes}")
    return report


def _is_https(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


def _valid_fernet_key(value: str | None) -> bool:
    if not value:
        return False
    try:
        Fernet(value.encode("ascii"))
    except (ValueError, TypeError):
        return False
    return True
