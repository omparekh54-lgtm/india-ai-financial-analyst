from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import get_settings

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_APPROVAL_REFERENCE = "SG-2026-08-31-01"
DEFAULT_TOKEN_ENV = "UPSTOX_DATA_ACCESS_TOKEN"
STAGE_ORDER = ("universe", "mappings", "market_context", "market_history")


@dataclass(frozen=True)
class CorpusStage:
    name: str
    command: tuple[str, ...]


def build_commands(
    *,
    python_executable: str,
    scripts_dir: Path,
    security_master_provider: str,
    nse_file: str | None,
    nse_url: str | None,
    upstox_security_master_file: str | None,
    upstox_security_master_url: str | None,
    upstox_mapping_file: str | None,
    upstox_mapping_url: str | None,
    min_rows: int,
    classification_delay_ms: int,
    classification_min_coverage_pct: float,
    mapping_min_coverage_pct: float,
    approval_reference: str,
    benchmark_min_rows: int,
    flow_max_age_days: int,
    vix_max_age_days: int,
    rbi_10y_max_age_days: int,
    repo_source_url: str,
    repo_date_column: str | None,
    repo_value_column: str | None,
    cpi_source_url: str,
    cpi_date_column: str | None,
    cpi_value_column: str | None,
    iip_source_url: str,
    iip_date_column: str | None,
    iip_value_column: str | None,
    history_from_date: date,
    history_to_date: date,
    history_batch_size: int,
    history_max_batches: int,
    access_token_env: str,
    history_request_delay_seconds: float,
    start_at: str = "universe",
) -> tuple[CorpusStage, ...]:
    provider = security_master_provider.strip().lower()
    if provider not in {"nse", "upstox"}:
        raise ValueError("security_master_provider must be nse or upstox")
    if min_rows < 1000:
        raise ValueError("min_rows must be >= 1000 for the production NSE universe")
    if classification_delay_ms < 100:
        raise ValueError("classification_delay_ms must be >= 100")
    if not 0 < classification_min_coverage_pct <= 100:
        raise ValueError("classification_min_coverage_pct must be > 0 and <= 100")
    if not 0 < mapping_min_coverage_pct <= 100:
        raise ValueError("mapping_min_coverage_pct must be > 0 and <= 100")
    if benchmark_min_rows < 2:
        raise ValueError("benchmark_min_rows must be >= 2")
    if flow_max_age_days < 0 or vix_max_age_days < 0 or rbi_10y_max_age_days < 0:
        raise ValueError("market-context freshness limits must be >= 0")
    if history_from_date > history_to_date:
        raise ValueError("history_from_date cannot be after history_to_date")
    if history_batch_size < 1 or history_batch_size > 500:
        raise ValueError("history_batch_size must be between 1 and 500")
    if history_max_batches < 1 or history_max_batches > 100:
        raise ValueError("history_max_batches must be between 1 and 100")
    if history_request_delay_seconds < 0 or history_request_delay_seconds > 10:
        raise ValueError("history_request_delay_seconds must be between 0 and 10")
    if start_at not in STAGE_ORDER:
        raise ValueError("start_at must be one of: " + ", ".join(STAGE_ORDER))
    if not approval_reference.strip():
        raise ValueError("approval_reference cannot be empty")
    if not access_token_env.strip():
        raise ValueError("access_token_env cannot be empty")
    for name, value in (
        ("repo_source_url", repo_source_url),
        ("cpi_source_url", cpi_source_url),
        ("iip_source_url", iip_source_url),
    ):
        if not value.strip():
            raise ValueError(f"{name} is required for a reproducible production macro corpus")

    if provider == "nse" and (upstox_security_master_file or upstox_security_master_url):
        raise ValueError("Upstox security-master options require provider=upstox")
    if provider == "upstox" and (nse_file or nse_url):
        raise ValueError("NSE security-master options require provider=nse")
    if nse_file and nse_url:
        raise ValueError("nse_file and nse_url are mutually exclusive")
    if upstox_security_master_file and upstox_security_master_url:
        raise ValueError("Upstox security-master file and URL are mutually exclusive")

    universe = [
        python_executable,
        str(scripts_dir / "bootstrap_nse_universe.py"),
        "--provider",
        provider,
        "--min-rows",
        str(min_rows),
        "--classification-delay-ms",
        str(classification_delay_ms),
        "--classification-min-coverage-pct",
        str(classification_min_coverage_pct),
    ]
    if provider == "nse":
        if nse_file:
            universe.extend(["--nse-file", nse_file])
        elif nse_url:
            universe.extend(["--nse-url", nse_url])
    else:
        universe.extend(["--upstox-approval-reference", approval_reference])
        if upstox_security_master_file:
            universe.extend(["--upstox-file", upstox_security_master_file])
        elif upstox_security_master_url:
            universe.extend(["--upstox-url", upstox_security_master_url])

    mappings = [
        python_executable,
        str(scripts_dir / "backfill_upstox_instrument_mappings.py"),
        "--min-rows",
        str(min_rows),
        "--min-coverage-pct",
        str(mapping_min_coverage_pct),
        "--approval-reference",
        approval_reference,
    ]
    if upstox_mapping_file:
        mappings.extend(["--file", upstox_mapping_file])
    if upstox_mapping_url:
        mappings.extend(["--url", upstox_mapping_url])

    market_context = [
        python_executable,
        str(scripts_dir / "bootstrap_india_market_context.py"),
        "--benchmark-min-rows",
        str(benchmark_min_rows),
        "--flow-max-age-days",
        str(flow_max_age_days),
        "--vix-max-age-days",
        str(vix_max_age_days),
        "--rbi-10y-max-age-days",
        str(rbi_10y_max_age_days),
        "--repo-source-url",
        repo_source_url,
        "--cpi-source-url",
        cpi_source_url,
        "--iip-source-url",
        iip_source_url,
    ]
    for flag, value in (
        ("--repo-date-column", repo_date_column),
        ("--repo-value-column", repo_value_column),
        ("--cpi-date-column", cpi_date_column),
        ("--cpi-value-column", cpi_value_column),
        ("--iip-date-column", iip_date_column),
        ("--iip-value-column", iip_value_column),
    ):
        if value:
            market_context.extend([flag, value])

    market_history = [
        python_executable,
        str(scripts_dir / "bootstrap_upstox_market_history_all.py"),
        "--from-date",
        history_from_date.isoformat(),
        "--to-date",
        history_to_date.isoformat(),
        "--batch-size",
        str(history_batch_size),
        "--max-batches",
        str(history_max_batches),
        "--access-token-env",
        access_token_env,
        "--approval-reference",
        approval_reference,
        "--request-delay-seconds",
        str(history_request_delay_seconds),
    ]

    stages = (
        CorpusStage("universe", tuple(universe)),
        CorpusStage("mappings", tuple(mappings)),
        CorpusStage("market_context", tuple(market_context)),
        CorpusStage("market_history", tuple(market_history)),
    )
    return stages[STAGE_ORDER.index(start_at) :]


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


def _run_stage(stage: CorpusStage) -> dict[str, Any]:
    completed = subprocess.run(
        stage.command,
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    result: dict[str, Any] = {
        "name": stage.name,
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "result": _parse_output(completed.stdout),
    }
    stderr = completed.stderr.strip()
    if stderr:
        result["stderr_tail"] = stderr[-4000:]
    return result


def _preflight(*, access_token_env: str) -> None:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured before production corpus mutation")
    if not settings.enable_external_data_calls:
        raise RuntimeError("ENABLE_EXTERNAL_DATA_CALLS must be true before production corpus mutation")
    if not settings.fred_api_key:
        raise RuntimeError("FRED_API_KEY must be configured before production corpus mutation")
    if not os.environ.get(access_token_env, "").strip():
        raise RuntimeError(
            f"{access_token_env} must contain the operator Upstox data-access token before any "
            "production corpus stage is executed"
        )


def main() -> int:
    today = datetime.now(UTC).date()
    parser = argparse.ArgumentParser(
        description=(
            "Build the production India market corpus in deterministic fail-closed order: genuine "
            "NSE universe + official taxonomy, exact-ISIN Upstox mappings, complete source-linked "
            "India macro/market context, then listing-age-aware sourced daily market history."
        )
    )
    parser.add_argument("--security-master-provider", choices=("nse", "upstox"), default="nse")
    nse_source = parser.add_mutually_exclusive_group()
    nse_source.add_argument("--nse-file")
    nse_source.add_argument("--nse-url")
    upstox_master_source = parser.add_mutually_exclusive_group()
    upstox_master_source.add_argument("--upstox-security-master-file")
    upstox_master_source.add_argument("--upstox-security-master-url")
    parser.add_argument("--upstox-mapping-file")
    parser.add_argument("--upstox-mapping-url")
    parser.add_argument("--min-rows", type=int, default=1000)
    parser.add_argument("--classification-delay-ms", type=int, default=350)
    parser.add_argument("--classification-min-coverage-pct", type=float, default=100.0)
    parser.add_argument("--mapping-min-coverage-pct", type=float, default=100.0)
    parser.add_argument("--approval-reference", default=DEFAULT_APPROVAL_REFERENCE)
    parser.add_argument("--benchmark-min-rows", type=int, default=30)
    parser.add_argument("--flow-max-age-days", type=int, default=7)
    parser.add_argument("--vix-max-age-days", type=int, default=7)
    parser.add_argument("--rbi-10y-max-age-days", type=int, default=45)
    parser.add_argument("--repo-source-url", required=True)
    parser.add_argument("--repo-date-column")
    parser.add_argument("--repo-value-column")
    parser.add_argument("--cpi-source-url", required=True)
    parser.add_argument("--cpi-date-column")
    parser.add_argument("--cpi-value-column")
    parser.add_argument("--iip-source-url", required=True)
    parser.add_argument("--iip-date-column")
    parser.add_argument("--iip-value-column")
    parser.add_argument(
        "--history-from-date",
        type=date.fromisoformat,
        default=today - timedelta(days=500),
    )
    parser.add_argument("--history-to-date", type=date.fromisoformat, default=today)
    parser.add_argument("--history-batch-size", type=int, default=250)
    parser.add_argument("--history-max-batches", type=int, default=20)
    parser.add_argument("--upstox-access-token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--history-request-delay-seconds", type=float, default=0.15)
    parser.add_argument("--start-at", choices=STAGE_ORDER, default="universe")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the exact production commands without network access or database mutation.",
    )
    args = parser.parse_args()

    try:
        stages = build_commands(
            python_executable=sys.executable,
            scripts_dir=SCRIPTS_DIR,
            security_master_provider=args.security_master_provider,
            nse_file=args.nse_file,
            nse_url=args.nse_url,
            upstox_security_master_file=args.upstox_security_master_file,
            upstox_security_master_url=args.upstox_security_master_url,
            upstox_mapping_file=args.upstox_mapping_file,
            upstox_mapping_url=args.upstox_mapping_url,
            min_rows=args.min_rows,
            classification_delay_ms=args.classification_delay_ms,
            classification_min_coverage_pct=args.classification_min_coverage_pct,
            mapping_min_coverage_pct=args.mapping_min_coverage_pct,
            approval_reference=args.approval_reference,
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
            history_from_date=args.history_from_date,
            history_to_date=args.history_to_date,
            history_batch_size=args.history_batch_size,
            history_max_batches=args.history_max_batches,
            access_token_env=args.upstox_access_token_env,
            history_request_delay_seconds=args.history_request_delay_seconds,
            start_at=args.start_at,
        )
    except ValueError as exc:
        parser.error(str(exc))

    summary: dict[str, object] = {
        "data_policy": "real_provenance_required_no_synthetic_fallback",
        "security_master_provider": args.security_master_provider,
        "start_at": args.start_at,
        "plan_only": args.plan_only,
        "stage_order": [stage.name for stage in stages],
        "required_macro_sources": ["repo_rate", "cpi_yoy", "iip_yoy"],
    }
    if args.plan_only:
        summary["commands"] = [
            {"name": stage.name, "command": list(stage.command)} for stage in stages
        ]
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    try:
        _preflight(access_token_env=args.upstox_access_token_env)
    except RuntimeError as exc:
        summary["status"] = "blocked_preflight"
        summary["error"] = str(exc)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 2

    results: list[dict[str, Any]] = []
    for stage in stages:
        result = _run_stage(stage)
        results.append(result)
        summary["stages"] = results
        if not result["ok"]:
            summary["status"] = "failed"
            summary["failed_stage"] = stage.name
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 1

    summary["status"] = "completed"
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
