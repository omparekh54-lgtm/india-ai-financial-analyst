from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from sqlalchemy import text

from app.core.config import get_settings
from app.db import create_database_engine

_ALLOWED_STATUS = ("pending", "approved", "rejected", "expired")
_ALLOWED_USE_CASE = ("internal_research", "user_display", "redistribution")


async def _run(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    if args.status == "approved" and not args.approval_reference.strip():
        raise ValueError("--approval-reference is required when status=approved")
    expires_at = datetime.fromisoformat(args.expires_at) if args.expires_at else None
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        insert into commercial_source_approvals (
                          provider, source_scope, use_case, status, approval_reference,
                          approved_at, expires_at, metadata, updated_at
                        ) values (
                          :provider, :source_scope, :use_case, :status, :approval_reference,
                          case when :status='approved' then now() else null end,
                          :expires_at, cast(:metadata as jsonb), now()
                        )
                        on conflict (provider, source_scope, use_case) do update
                        set status=excluded.status,
                            approval_reference=excluded.approval_reference,
                            approved_at=case
                              when excluded.status='approved' then coalesce(
                                commercial_source_approvals.approved_at,
                                now()
                              )
                              else null
                            end,
                            expires_at=excluded.expires_at,
                            metadata=excluded.metadata,
                            updated_at=now()
                        returning id, provider, source_scope, use_case, status,
                                  approval_reference, approved_at, expires_at, updated_at
                        """
                    ),
                    {
                        "provider": args.provider.strip().upper(),
                        "source_scope": args.source_scope.strip(),
                        "use_case": args.use_case,
                        "status": args.status,
                        "approval_reference": args.approval_reference.strip() or None,
                        "expires_at": expires_at,
                        "metadata": json.dumps(
                            {
                                "operator_note": args.note.strip() if args.note else None,
                                "recorded_by": "set_commercial_source_approval.py",
                            }
                        ),
                    },
                )
            ).mappings().one()
    finally:
        await engine.dispose()
    return {key: str(value) if value is not None else None for key, value in row.items()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Record an explicit commercial source-use approval. This records operator/legal "
            "evidence only; it cannot manufacture or infer licensing permission."
        )
    )
    parser.add_argument("--provider", required=True)
    parser.add_argument("--source-scope", required=True)
    parser.add_argument("--use-case", choices=_ALLOWED_USE_CASE, default="user_display")
    parser.add_argument("--status", choices=_ALLOWED_STATUS, required=True)
    parser.add_argument("--approval-reference", default="")
    parser.add_argument("--expires-at", help="ISO-8601 timestamp when approval expires")
    parser.add_argument("--note")
    args = parser.parse_args()
    if not args.provider.strip() or not args.source_scope.strip():
        parser.error("--provider and --source-scope cannot be blank")
    try:
        payload = asyncio.run(_run(args))
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"status": "completed", "approval": payload}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
