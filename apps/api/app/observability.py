from __future__ import annotations

import logging
import os
from collections.abc import Mapping

import sentry_sdk

from app.core.config import Settings

logger = logging.getLogger(__name__)


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


def capture_browser_error(
    settings: Settings,
    *,
    kind: str,
    message: str,
    page_path: str,
    stack: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> bool:
    """Capture a sanitized authenticated-browser failure through the server-side SDK."""
    # Browser exception text, stacks and metadata may contain tokens or research queries.
    # Deliberately discard free-form fields rather than relying on incomplete regex redaction.
    safe_kind = kind if kind in {"window_error", "unhandled_rejection"} else "browser_error"
    safe_path = page_path if page_path in {"/", "/watchlists"} else "/other"
    safe_message = f"Frontend {safe_kind} on {safe_path}"
    context = {"kind": safe_kind, "page_path": safe_path}
    if not settings.sentry_dsn:
        logger.error("%s", safe_message)
        return False
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("error.origin", "browser")
        scope.set_context("browser_error", context)
        sentry_sdk.capture_message(safe_message, level="error")
    return True
