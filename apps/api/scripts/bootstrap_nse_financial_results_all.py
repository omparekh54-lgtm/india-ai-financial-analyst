from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.core.agent_data_readiness import load_agent_data_coverage
from app.core.config import get_settings
from app.core.financial_history_coverage import load_financial_history_coverage
from app.db import create_database_engine

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent


def _parse_output(stdout: str) -> object:
    text_value = stdout.strip()
    if not text_value:
        return None
    try:
        return json.loads(text_value)
    except json.JSONDecodeError:
        for line in reversed(text_value.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"stdout_tail": text_value[-4000:]}


def _run_batch(command: tuple[str, ...]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    result: dict[str, Any] = {
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "result": _parse_output(completed.stdout),
    }
    stderr = completed.stderr.strip()
    if stderr:
        result["stderr_tail"] = stderr[-4000:]
    return result


def build_batch_command(
    *,
    python_executable: str,
    scripts_dir: Path,
    batch_size: int,
    after_symbol: str | None,
    max_periods: int,
    min_selected_periods: int,
    request_delay_seconds: float,
    document_delay_seconds: float,
    refresh_all: bool,
    dry_run: bool,
) -> tuple[str, ...]:
    if batch_size < 1 or batch_size > 100:
        raise ValueError("batch_size must be between 1 and 100")
    if max_periods < 1 or max_periods > 20:
        raise ValueError("max_periods must be between 1 and 20")
    if min_selected_periods < 0 or min_selected_periods > max_periods:
        raise ValueError("min_selected_periods must be between 0 and max_periods")
    if request_delay_seconds < 0 or request_delay_seconds > 10:
        raise ValueError("request_delay_seconds must be between 0 and 10")
    if document_delay_seconds < 0 or document_delay_seconds > 10:
        raise ValueError("document_delay_seconds must be between 0 and 10")

    command = [
        python_executable,
        str(scripts_dir / "backfill_nse_financial_results.py"),
        "--all",
        "--limit",
        str(batch_size),
        "--max-periods",
        str(max_periods),
        "--min-selected-periods",
        str(min_selected_periods),
        "--request-delay-seconds",
        str(request_delay_seconds),
        "--document-delay-seconds",
        str(document_delay_seconds),
    ]
    if after_symbol:
        command.extend(["--after-symbol", after_symbol])
    if refresh_all:
        command.append("--refresh-all")
    if dry_run:
        command.append("--dry-run")
    return tuple(command)


async def _postflight(database_url: str) -> dict[str, object]:
    engine = create_database_engine(database_url)
    try:
        financial = await load_financial_history_coverage(engine)
        agents = await load_agent_data_coverage(engine)
    finally:
        await engine.dispose()

    total = agents.nse_eq_securities
    filing_complete = total > 0 and agents.recent_filing_evidence_securities == total
    earnings_complete = total > 0 and agents.recent_earnings_evidence_securities == total
    complete = financial.complete and filing_complete and earnings_complete
    return {
        "complete": complete,
        "financial_history": financial.as_dict(),
        "recent_filing_evidence_securities": agents.recent_filing_evidence_securities,
        "recent_filing_coverage_pct": _coverage_pct(
            agents.recent_filing_evidence_securities,
            total,
        ),
        "recent_earnings_evidence_securities": agents.recent_earnings_evidence_securities,
        "recent_earnings_coverage_pct": _coverage_pct(
            agents.recent_earnings_evidence_securities,
            total,
        ),
        "nse_eq_securities": total,
    }


def _coverage_pct(covered: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((covered / total) * 100.0, 2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Populate official NSE financial-result XBRL across the supported NSE equity universe "
            "in bounded resumable batches, then enforce listing-aware financial, filing and "
            "earnings-evidence postflight coverage."
        )
    )
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-batches", type=int, default=100)
    parser.add_argument("--start-after-symbol")
    parser.add_argument("--max-periods", type=int, default=10)
    parser.add_argument(
        "--min-selected-periods",
        type=int,
        default=0,
        help="0 uses listing-age-aware requirements; positive values make the floor stricter.",
    )
    parser.add_argument("--request-delay-seconds", type=float, default=0.35)
    parser.add_argument("--document-delay-seconds", type=float, default=0.10)
    parser.add_argument("--refresh-all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.max_batches < 1 or args.max_batches > 200:
        parser.error("--max-batches must be between 1 and 200")
    try:
        build_batch_command(
            python_executable=sys.executable,
            scripts_dir=SCRIPTS_DIR,
            batch_size=args.batch_size,
            after_symbol=args.start_after_symbol,
            max_periods=args.max_periods,
            min_selected_periods=args.min_selected_periods,
            request_delay_seconds=args.request_delay_seconds,
            document_delay_seconds=args.document_delay_seconds,
            refresh_all=args.refresh_all,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        parser.error(str(exc))

    settings = get_settings()
    if not settings.database_url:
        parser.error("DATABASE_URL must be configured")

    cursor = args.start_after_symbol.upper() if args.start_after_symbol else None
    batches: list[dict[str, Any]] = []
    status = "max_batches_reached"
    for batch_number in range(1, args.max_batches + 1):
        command = build_batch_command(
            python_executable=sys.executable,
            scripts_dir=SCRIPTS_DIR,
            batch_size=args.batch_size,
            after_symbol=cursor,
            max_periods=args.max_periods,
            min_selected_periods=args.min_selected_periods,
            request_delay_seconds=args.request_delay_seconds,
            document_delay_seconds=args.document_delay_seconds,
            refresh_all=args.refresh_all,
            dry_run=args.dry_run,
        )
        result = _run_batch(command)
        batch_summary: dict[str, Any] = {
            "batch_number": batch_number,
            "after_symbol": cursor,
            **result,
        }
        batches.append(batch_summary)
        if not result["ok"]:
            output = {
                "status": "failed",
                "failed_batch": batch_number,
                "cursor": cursor,
                "batches": batches,
            }
            print(json.dumps(output, indent=2, sort_keys=True, default=str))
            return 1

        payload = result.get("result")
        if not isinstance(payload, dict):
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failed_batch": batch_number,
                        "error": "financial worker returned no structured JSON payload",
                        "batches": batches,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )
            return 1
        target_count = int(payload.get("target_count") or 0)
        next_cursor = str(payload.get("next_after_symbol") or "").strip().upper() or None
        if target_count == 0:
            status = "completed"
            break
        if next_cursor is None or next_cursor == cursor:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failed_batch": batch_number,
                        "error": "financial corpus cursor did not advance",
                        "cursor": cursor,
                        "batches": batches,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )
            return 1
        cursor = next_cursor

    postflight = asyncio.run(_postflight(settings.database_url))
    output = {
        "status": status,
        "data_policy": "real_primary_xbrl_no_synthetic_fallback",
        "dry_run": args.dry_run,
        "batch_count": len(batches),
        "next_after_symbol": cursor,
        "batches": batches,
        "postflight": postflight,
    }
    print(json.dumps(output, indent=2, sort_keys=True, default=str))

    if args.dry_run:
        return 0 if status == "completed" else 1
    if status != "completed" or postflight.get("complete") is not True:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
