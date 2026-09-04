from __future__ import annotations

from unittest.mock import patch

from app.core.config import Settings
from app.observability import configure_sentry


def test_sentry_is_disabled_without_dsn() -> None:
    with patch("app.observability.sentry_sdk.init") as init:
        assert configure_sentry(Settings(), service="research-worker") is False
    init.assert_not_called()


def test_sentry_tags_worker_and_disables_default_pii(monkeypatch) -> None:
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    with (
        patch("app.observability.sentry_sdk.init") as init,
        patch("app.observability.sentry_sdk.set_tag") as set_tag,
    ):
        assert configure_sentry(
            Settings(sentry_dsn="https://public@example.ingest.sentry.io/1"),
            service="live-market-worker",
        ) is True

    init.assert_called_once_with(
        dsn="https://public@example.ingest.sentry.io/1",
        environment="development",
        release="abc123",
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    set_tag.assert_called_once_with("service", "live-market-worker")
