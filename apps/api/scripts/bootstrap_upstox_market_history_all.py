from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.db import create_database_engine

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_APPROVAL_REFERENCE = "SG-2026-08-31-01"
DEFAULT_TOKEN_ENV = "UPSTOX_DATA_ACCESS_TOKEN"


def build_batch_command(
    *,
    python_executable: str,
    scripts_dir: Path,
    from_date: date,
    to_date: date,
    batch_size: int,
    after_symbol: str | None,
    access_token_env: str,
    approval_reference: str,
    request_delay_seconds: float,
    dry_run: bool,
) -> list[str]:
    if from_date > to_date:
        raise ValueError("from_date cannot be after to_date")
    if batch_size < 1 or batch_size > 500:
        raise ValueError("batch_size must be between 1 and 500")
    if request_delay_seconds < 0 or request_delay_seconds > 10:
        raise ValueError("request_delay_seconds must be between 0 and 10")
    command = [
        python_executable,
        str(scripts_dir / "backfill_upstox_market_history.py"),
        "--all",
        "--from-date",
        from_date.isoformat(),
        "--to-date",
        to_date.isoformat(),
        "--limit",
        str(batch_size),
        "--access-token-env",
        access_token_env,
        "--approval-reference",
        approval_reference,
        "--request-delay-seconds",
        str(request_delay_seconds),
    ]
    if after_symbol:
        command.extend(["--after-symbol", after_symbol.upper()])
    if dry_run:
        command.append("--dry-run")
    return command


def _parse_output(stdout: str) -> dict[str, Any]:
    text_value = stdout.strip()
    if not text_value:
        raise ValueError("Upstox history batch returned no JSON output")
    try:
        payload = json.loads(text_value)
    except json.JSONDecodeError as exc:
        raise ValueError("Upstox history batch returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError("Upstox history batch output must be a JSON object")
    return payload


def _next_cursor(payload: dict[str, Any], *, dry_run: bool) -> str | None:
    if dry_run:
        targets = payload.get("targets")
        if not isinstance(targets, list) or not targets:
            return None
        last = targets[-1]
        if not isinstance(last, dict) or not str(last.get("symbol") or "").strip():
            raise ValueError("Dry-run batch is missing its final target symbol")
        return str(last["symbol"]).strip().upper()
    cursor = payload.get("next_after_symbol")
    if cursor is None:
        return None
    normalized = str(cursor).strip().upper()
    return normalized or None


async def _coverage(min_bars: int) -> dict[str, int]:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        """
                        with nse_eq as (
                          select id
                          from securities
                          where primary_exchange = 'NSE'
                            and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
                        ), covered as (
                          select mb.security_id
                          from market_bars mb
                          join nse_eq n on n.id = mb.security_id
                          where mb.source_id is not null
                            and mb.interval in ('1d', 'day', 'daily')
                          group by mb.security_id
                          having count(distinct mb.ts::date) >= :min_bars
                        )
                        select
                          (select count(*) from nse_eq) as total,
                          (select count(*) from covered) as covered
                        """
                    ),
                    {"min_bars": min_bars},
                )
            ).mappings().one()
        return {
            "total": int(row["total"] or 0),
            "covered": int(row["covered"] or 0),
        }
    finally:
        await engine.dispose()


def main() -> int:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(
        description=(
            "Paginate the approved Upstox daily-history backfill across the full mapped NSE EQ "
            "universe, fail fast on any batch error and verify sourced-bar coverage afterward."
        )
    )
    parser.add_argument("--from-date", type=date.fromisoformat, default=today - timedelta(days=500))
    parser.add_argument("--to-date", type=date.fromisoformat, default=today)
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--max-batches", type=int, default=20)
    parser.add_argument("--min-bars", type=int, default=200)
    parser.add_argument("--access-token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--approval-reference", default=DEFAULT_APPROVAL_REFERENCE)
    parser.add_argument("--request-delay-seconds", type=float, default=0.15)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.max_batches < 1 or args.max_batches > 100:
        parser.error("--max-batches must be between 1 and 100")
    if args.min_bars < 30 or args.min_bars > 500:
        parser.error("--min-bars must be between 30 and 500")

    cursor: str | None = None
    batches: list[dict[str, object]] = []
    total_targets = 0
    seen_cursors: set[str] = set()

    for batch_number in range(1, args.max_batches + 1):
        try:
            command = build_batch_command(
                python_executable=sys.executable,
                scripts_dir=SCRIPTS_DIR,
                from_date=args.from_date,
                to_date=args.to_date,
                batch_size=args.batch_size,
                after_symbol=cursor,
                access_token_env=args.access_token_env,
                approval_reference=args.approval_reference,
                request_delay_seconds=args.request_delay_seconds,
                dry_run=args.dry_run,
            )
        except ValueError as exc:
            parser.error(str(exc))

        completed = subprocess.run(
            command,
            cwd=API_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            payload = _parse_output(completed.stdout)
        except (TypeError, ValueError) as exc:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failed_batch": batch_number,
                        "error": str(exc),
                        "stderr_tail": completed.stderr.strip()[-4000:],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 1

        target_count = int(payload.get("target_count") or 0)
        total_targets += target_count
        batches.append(
            {
                "batch": batch_number,
                "target_count": target_count,
                "status": payload.get("status"),
                "failure_count": int(payload.get("failure_count") or 0),
            }
        )
        if completed.returncode != 0:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failed_batch": batch_number,
                        "batches": batches,
                        "batch_result": payload,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )
            return 1
        if target_count == 0:
            break

        next_cursor = _next_cursor(payload, dry_run=args.dry_run)
        if next_cursor is None:
            break
        if next_cursor in seen_cursors:
            raise SystemExit(f"Upstox history pagination cursor repeated: {next_cursor}")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if target_count < args.batch_size:
            break
    else:
        raise SystemExit(
            f"Reached --max-batches={args.max_batches} before exhausting the NSE universe"
        )

    summary: dict[str, object] = {
        "status": "dry_run" if args.dry_run else "completed",
        "provider": "upstox",
        "provenance_class": "licensed_or_approved",
        "from_date": args.from_date.isoformat(),
        "to_date": args.to_date.isoformat(),
        "batch_size": args.batch_size,
        "batch_count": len(batches),
        "total_targets": total_targets,
        "min_bars": args.min_bars,
        "batches": batches,
    }
    if not args.dry_run:
        coverage = asyncio.run(_coverage(args.min_bars))
        summary["coverage"] = coverage
        if coverage["covered"] != coverage["total"]:
            summary["status"] = "incomplete_coverage"
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 2

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
