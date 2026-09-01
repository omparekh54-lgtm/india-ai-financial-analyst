from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent


def _rbi_command(
    *,
    python_executable: str,
    scripts_dir: Path,
    series_key: str,
    source_url: str | None,
    unit: str,
    date_column: str | None,
    value_column: str | None,
    dry_run: bool,
) -> list[str] | None:
    if not source_url:
        return None
    command = [
        python_executable,
        str(scripts_dir / "backfill_rbi_macro_url.py"),
        "--series-key",
        series_key,
        "--source-url",
        source_url,
        "--unit",
        unit,
        "--min-rows",
        "1",
    ]
    if date_column:
        command.extend(["--date-column", date_column])
    if value_column:
        command.extend(["--value-column", value_column])
    if dry_run:
        command.append("--dry-run")
    return command


def build_commands(
    *,
    python_executable: str,
    scripts_dir: Path,
    benchmark_min_rows: int,
    flow_max_age_days: int,
    vix_max_age_days: int,
    rbi_10y_max_age_days: int,
    repo_source_url: str | None = None,
    repo_date_column: str | None = None,
    repo_value_column: str | None = None,
    cpi_source_url: str | None = None,
    cpi_date_column: str | None = None,
    cpi_value_column: str | None = None,
    iip_source_url: str | None = None,
    iip_date_column: str | None = None,
    iip_value_column: str | None = None,
    dry_run: bool = False,
) -> list[tuple[str, list[str]]]:
    if benchmark_min_rows < 2:
        raise ValueError("benchmark_min_rows must be >= 2")
    if flow_max_age_days < 0:
        raise ValueError("flow_max_age_days must be >= 0")
    if vix_max_age_days < 0:
        raise ValueError("vix_max_age_days must be >= 0")
    if rbi_10y_max_age_days < 0:
        raise ValueError("rbi_10y_max_age_days must be >= 0")

    fred_macro = [
        python_executable,
        str(scripts_dir / "bootstrap_fred_macro.py"),
        "--series",
        "usd_inr",
        "--series",
        "brent",
        "--history-days",
        "400",
        "--min-rows",
        "30",
    ]
    if dry_run:
        fred_macro.append("--dry-run")

    commands: list[tuple[str, list[str]]] = [("fred_usdinr_brent", fred_macro)]
    for name, command in (
        (
            "rbi_repo_rate",
            _rbi_command(
                python_executable=python_executable,
                scripts_dir=scripts_dir,
                series_key="repo_rate",
                source_url=repo_source_url,
                unit="percent",
                date_column=repo_date_column,
                value_column=repo_value_column,
                dry_run=dry_run,
            ),
        ),
        (
            "rbi_cpi_yoy",
            _rbi_command(
                python_executable=python_executable,
                scripts_dir=scripts_dir,
                series_key="cpi_yoy",
                source_url=cpi_source_url,
                unit="percent",
                date_column=cpi_date_column,
                value_column=cpi_value_column,
                dry_run=dry_run,
            ),
        ),
        (
            "rbi_iip_yoy",
            _rbi_command(
                python_executable=python_executable,
                scripts_dir=scripts_dir,
                series_key="iip_yoy",
                source_url=iip_source_url,
                unit="percent",
                date_column=iip_date_column,
                value_column=iip_value_column,
                dry_run=dry_run,
            ),
        ),
    ):
        if command is not None:
            commands.append((name, command))

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
        commands.extend(
            [
                ("nse_benchmarks", benchmarks),
                ("nse_fii_dii_flows", flows),
                ("rbi_10y", rbi_10y),
            ]
        )
        return commands

    vix_sync = [
        python_executable,
        str(scripts_dir / "sync_india_vix_macro.py"),
        "--max-age-days",
        str(vix_max_age_days),
    ]
    commands.extend(
        [
            ("nse_benchmarks", benchmarks),
            ("india_vix_macro_sync", vix_sync),
            ("nse_fii_dii_flows", flows),
            ("rbi_10y", rbi_10y),
        ]
    )
    return commands


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
            "Bootstrap required India market/macro context from source-linked data: FRED USD/INR "
            "+ Brent, optional explicit RBI repo/CPI/IIP exports, official NSE benchmark/VIX/flows, "
            "and RBI India 10Y. Production orchestration supplies the RBI URLs explicitly."
        )
    )
    parser.add_argument("--benchmark-min-rows", type=int, default=30)
    parser.add_argument("--flow-max-age-days", type=int, default=7)
    parser.add_argument("--vix-max-age-days", type=int, default=7)
    parser.add_argument("--rbi-10y-max-age-days", type=int, default=45)
    parser.add_argument("--repo-source-url")
    parser.add_argument("--repo-date-column")
    parser.add_argument("--repo-value-column")
    parser.add_argument("--cpi-source-url")
    parser.add_argument("--cpi-date-column")
    parser.add_argument("--cpi-value-column")
    parser.add_argument("--iip-source-url")
    parser.add_argument("--iip-date-column")
    parser.add_argument("--iip-value-column")
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
            repo_source_url=args.repo_source_url,
            repo_date_column=args.repo_date_column,
            repo_value_column=args.repo_value_column,
            cpi_source_url=args.cpi_source_url,
            cpi_date_column=args.cpi_date_column,
            cpi_value_column=args.cpi_value_column,
            iip_source_url=args.iip_source_url,
            iip_date_column=args.iip_date_column,
            iip_value_column=args.iip_value_column,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        parser.error(str(exc))

    summary: dict[str, object] = {
        "data_policy": "real_source_linked_data_only",
        "providers": ["FRED", "NSE", "RBI"],
        "dry_run": args.dry_run,
        "stages": [],
        "explicit_rbi_series": {
            "repo_rate": bool(args.repo_source_url),
            "cpi_yoy": bool(args.cpi_source_url),
            "iip_yoy": bool(args.iip_source_url),
        },
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
