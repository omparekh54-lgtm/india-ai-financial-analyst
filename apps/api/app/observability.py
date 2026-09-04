from __future__ import annotations

import os

import sentry_sdk

from app.core.config import Settings


def configure_sentry(settings: Settings, *, service: str) -> bool:
    """Configure process-level error reporting without leaking request or broker data."""
    if not settings.sentry_dsn:
        return False

    release = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("VERCEL_GIT_COMMIT_SHA")
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=release,
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", service)
    return True
