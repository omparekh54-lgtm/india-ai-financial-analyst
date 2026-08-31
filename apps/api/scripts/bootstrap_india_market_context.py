from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent


def build_commands(
    *,
    python_executable: str,
    scripts_dir: Path,
    benchmark_min_rows: int,
    flow_max_age_days: int,
    vix_max_age_days: int,
    rbi_10y_max_age_days: int,
    dry_run: bool,
) -> list[tuple[str, list[str]]]:
    if benchmark_min_rows < 2:
        raise ValueError("benchmark_min_rows must be >= 2")
    if flow_max_age_days < 0:
        raise ValueError("flow_max_age_days must be >= 0")
    if vix_max_age_days < 0:
        raise ValueError("vix_max_age_days must be >= 0")
    if rbi_10y_max_age_days < 0:
        raise ValueError("rbi_10y_max_age_days must be >= 0")

    benchmarks = [
        python_executable,
        str(scripts_dir / "backfill_nse_benchmarks.py"),
        "--min-rows",
        str(benchmark_min_rows),
    ]
    flows = [
        python_executable,
        str(scripts_dir / "backfill_nse_fii_dii.py"),
        "--max-age-days",
        str(flow_max_age_days),
    ]
    rbi_10y = [
        python_executable,
        str(scripts_dir / "backfill_rbi_10y.py"),
        "--max-age-days",
        str(rbi_10y_max_age_days),
    ]

    if dry_run:
        for command in (benchmarks, flows, rbi_10y):
            command.append("--dry-run")
        return [
            ("nse_benchmarks", benchmarks),
            ("nse_fii_dii_flows", flows),
            ("rbi_10y", rbi_10y),
        ]

    vix_sync = [
        python_executable,
        str(scripts_dir / "sync_india_vix_macro.py"),
        "--max-age-days",
        str(vix_max_age_days),
    ]
    return [
        ("nse_benchmarks", benchmarks),
        ("india_vix_macro_sync", vix_sync),
        ("nse_fii_dii_flows", flows),
        ("rbi_10y", rbi_10y),
    ]


def _parse_output(stdout: str) -> object:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"stdout_tail": text[-4000:]}


def _run_stage(name: str, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    result: dict[str, Any] = {
        "name": name,
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "result": _parse_output(completed.stdout),
    }
    if completed.stderr.strip():
        result["stderr_tail"] = completed.stderr.strip()[-4000:]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap the required India market/macro context from official NSE and RBI sources: "
            "NIFTY 50 + India VIX history, India VIX macro sync, FII/DII cash flows and India 10Y."
        )
    )
    parser.add_argument("--benchmark-min-rows", type=int, default=30)
    parser.add_argument("--flow-max-age-days", type=int, default=7)
    parser.add_argument("--vix-max-age-days", type=int, default=7)
    parser.add_argument("--rbi-10y-max-age-days", type=int, default=45)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        commands = build_commands(
            python_executable=sys.executable,
            scripts_dir=SCRIPTS_DIR,
            benchmark_min_rows=args.benchmark_min_rows,
            flow_max_age_days=args.flow_max_age_days,
            vix_max_age_days=args.vix_max_age_days,
            rbi_10y_max_age_days=args.rbi_10y_max_age_days,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        parser.error(str(exc))

    summary: dict[str, object] = {
        "data_policy": "real_official_provider_data_only",
        "providers": ["NSE", "RBI"],
        "dry_run": args.dry_run,
        "stages": [],
    }
    if args.dry_run:
        summary["india_vix_macro_sync"] = (
            "skipped because benchmark dry-run performs no database writes"
        )

    results: list[dict[str, Any]] = []
    for name, command in commands:
        result = _run_stage(name, command)
        results.append(result)
        summary["stages"] = results
        if not result["ok"]:
            summary["failed_stage"] = name
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 1

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
