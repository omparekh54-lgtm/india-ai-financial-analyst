from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.peer_metric_coverage import MIN_COMPARABLE_METRICS, load_peer_metric_coverage
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


def build_batch_command(
    *,
    python_executable: str,
    scripts_dir: Path,
    batch_size: int,
    after_symbol: str | None,
    min_metrics: int,
    refresh_all: bool,
    dry_run: bool,
) -> tuple[str, ...]:
    if not 1 <= batch_size <= 250:
        raise ValueError("batch_size must be between 1 and 250")
    if not 1 <= min_metrics <= 6:
        raise ValueError("min_metrics must be between 1 and 6")

    command = [
        python_executable,
        str(scripts_dir / "backfill_derived_security_metrics.py"),
        "--all",
        "--limit",
        str(batch_size),
        "--min-metrics",
        str(min_metrics),
    ]
    if after_symbol:
        command.extend(["--after-symbol", after_symbol.upper()])
    if refresh_all:
        command.append("--refresh-all")
    if dry_run:
        command.append("--dry-run")
    return tuple(command)


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


async def _postflight(database_url: str) -> dict[str, object]:
    engine = create_database_engine(database_url)
    try:
        coverage = await load_peer_metric_coverage(engine)
    finally:
        await engine.dispose()
    return coverage.as_dict()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Derive source-linked comparable metrics across the full supported NSE EQ universe "
            "in bounded resumable batches and fail closed unless peer-metric coverage is complete."
        )
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=100)
    parser.add_argument("--start-after-symbol")
    parser.add_argument("--min-metrics", type=int, default=MIN_COMPARABLE_METRICS)
    parser.add_argument("--refresh-all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.max_batches <= 200:
        parser.error("--max-batches must be between 1 and 200")
    try:
        build_batch_command(
            python_executable=sys.executable,
            scripts_dir=SCRIPTS_DIR,
            batch_size=args.batch_size,
            after_symbol=args.start_after_symbol,
            min_metrics=args.min_metrics,
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
            min_metrics=args.min_metrics,
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
        payload = result.get("result")
        if not result["ok"]:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failed_batch": batch_number,
                        "cursor": cursor,
                        "batches": batches,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )
            return 1
        if not isinstance(payload, dict):
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "failed_batch": batch_number,
                        "error": "peer-metric worker returned no structured JSON payload",
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
                        "error": "peer-metric corpus cursor did not advance",
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
        "data_policy": "deterministic_source_linked_inputs_no_estimated_or_synthetic_fallback",
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
