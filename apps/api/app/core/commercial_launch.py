from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.operations_health import load_operations_health
from app.core.readiness import audit_settings


@dataclass(frozen=True)
class CommercialLaunchReport:
    ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    approvals: tuple[dict[str, object], ...]
    operations: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "approvals": list(self.approvals),
            "operations": self.operations,
        }


async def evaluate_commercial_launch(
    engine: AsyncEngine,
    settings: Settings,
    *,
    min_nse_eq_securities: int = 1000,
) -> CommercialLaunchReport:
    errors: list[str] = []
    warnings: list[str] = []
    config = audit_settings(settings)
    if settings.app_env.strip().lower() != "production":
        errors.append("APP_ENV must be production for the commercial launch gate.")
    if not settings.commercial_launch_enabled:
        errors.append("COMMERCIAL_LAUNCH_ENABLED must be explicitly enabled.")
    if settings.commercial_require_free_only and not settings.free_only:
        errors.append("FREE_ONLY must remain true for the configured free-first commercial launch.")
    errors.extend(issue.message for issue in config.errors)
    warnings.extend(issue.message for issue in config.warnings)

    operations = await load_operations_health(
        engine,
        settings,
        min_nse_eq_securities=min_nse_eq_securities,
    )
    if not operations.ready:
        errors.append("Production operations/corpus health is not green.")

    required = settings.commercial_required_source_scope_list
    approval_rows: list[dict[str, object]] = []
    now = datetime.now(UTC)
    async with engine.connect() as connection:
        for provider, scope in required:
            row = (
                await connection.execute(
                    text(
                        """
                        select provider, source_scope, use_case, status, approval_reference,
                               approved_at, expires_at, metadata
                        from commercial_source_approvals
                        where upper(provider)=upper(:provider)
                          and source_scope=:source_scope
                          and use_case='user_display'
                        """
                    ),
                    {"provider": provider, "source_scope": scope},
                )
            ).mappings().one_or_none()
            if row is None:
                approval_rows.append(
                    {
                        "provider": provider,
                        "source_scope": scope,
                        "status": "missing",
                        "ready": False,
                    }
                )
                errors.append(f"Commercial source approval missing for {provider}:{scope}.")
                continue
            expires_at = row["expires_at"]
            expired = expires_at is not None and expires_at <= now
            approved = (
                row["status"] == "approved"
                and bool(str(row["approval_reference"] or "").strip())
                and not expired
            )
            approval_rows.append(
                {
                    "provider": row["provider"],
                    "source_scope": row["source_scope"],
                    "status": "expired" if expired else row["status"],
                    "approval_reference": row["approval_reference"],
                    "approved_at": _iso(row["approved_at"]),
                    "expires_at": _iso(expires_at),
                    "ready": approved,
                }
            )
            if not approved:
                errors.append(f"Commercial source approval is not active for {provider}:{scope}.")

    if not required:
        errors.append(
            "COMMERCIAL_REQUIRED_SOURCE_SCOPES is empty; commercial launch must explicitly list "
            "every source/provider scope requiring user-display approval."
        )
    if not settings.enable_usage_limits:
        warnings.append(
            "Usage limits are disabled. This is allowed for controlled launch, but quota protection is inactive."
        )
    return CommercialLaunchReport(
        ready=not errors,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        approvals=tuple(approval_rows),
        operations=operations.as_dict(),
    )


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)
