from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.readiness import assert_production_ready, audit_settings


def _codes(settings: Settings) -> set[str]:
    return {issue.code for issue in audit_settings(settings).errors}


def test_development_defaults_are_not_blocked() -> None:
    report = audit_settings(Settings())
    assert report.ready is True
    assert report.errors == ()


def test_production_requires_database_auth_and_https_origins() -> None:
    settings = Settings(
        app_env="production",
        web_app_url="http://example.com",
        cors_origins="http://example.com",
    )
    codes = _codes(settings)
    assert "database_missing" in codes
    assert "auth_missing" in codes
    assert "web_app_https" in codes
    assert "cors_https" in codes


def test_gemini_dependent_features_fail_closed_without_gemini() -> None:
    settings = Settings(
        enable_external_llm_calls=True,
        groq_api_key="test-groq",
        enable_multimodal_document_analysis=True,
        enable_audio_transcription=True,
    )
    codes = _codes(settings)
    assert "multimodal_gemini_missing" in codes
    assert "audio_gemini_missing" in codes


def test_live_market_requires_complete_encrypted_upstox_configuration() -> None:
    settings = Settings(enable_live_market=True, upstox_client_id="client")
    assert "live_market_credentials_missing" in _codes(settings)

    invalid_key = Settings(
        enable_live_market=True,
        upstox_client_id="client",
        upstox_client_secret="secret",
        upstox_redirect_uri="https://api.example.com/v1/brokers/upstox/callback",
        broker_token_encryption_key="not-a-fernet-key",
    )
    assert "broker_encryption_invalid" in _codes(invalid_key)


def test_valid_production_core_configuration_is_ready() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+psycopg://user:pass@db.example.com/app",
        supabase_url="https://project.supabase.co",
        supabase_publishable_key="publishable-test",
        web_app_url="https://app.example.com",
        cors_origins="https://app.example.com",
        broker_token_encryption_key=Fernet.generate_key().decode("ascii"),
    )
    report = audit_settings(settings)
    assert report.ready is True
    assert {issue.code for issue in report.warnings} == {"sentry_missing"}


def test_production_assertion_raises_on_unsafe_configuration() -> None:
    with pytest.raises(RuntimeError, match="Unsafe production configuration"):
        assert_production_ready(Settings(app_env="production"))
