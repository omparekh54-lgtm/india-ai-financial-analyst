from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.db import create_database_engine
from app.ingestion.bootstrap import (
    build_bootstrap_plan,
    parse_benchmark_spec,
    parse_financial_spec,
    parse_macro_spec,
    parse_market_spec,
    parse_metrics_spec,
)

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_GOVERNANCE_REFERENCE = "SG-2026-08-31-01"


def _parse_stage_output(stdout: str) -> object:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for line in reversed(text.splitlines()):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {"stdout_tail": text[-4000:]}


def _run_stage(name: str, command: tuple[str, ...]) -> dict[str, Any]:
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
        "result": _parse_stage_output(completed.stdout),
    }
    stderr = completed.stderr.strip()
    if stderr:
        result["stderr_tail"] = stderr[-4000:]
    return result


async def _coverage_report() -> dict[str, object]:
    settings = get_settings()
    if not settings.database_url:
        return {
            "ready": False,
            "errors": ["DATABASE_URL is not configured."],
            "warnings": [],
            "coverage": {},
        }

    engine = create_database_engine(settings.database_url)
    try:
        coverage = await load_data_coverage(engine)
    finally:
        await engine.dispose()
    return evaluate_data_coverage(coverage).as_dict()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap approved real research data in deterministic order. Production bootstrap "
            "requires explicit provenance and does not accept synthetic/mock/sample sources."
        )
    )
    parser.add_argument("--skip-nse", action="store_true", help="Skip the security-master stage")
    parser.add_argument(
        "--security-master-provider",
        choices=("nse", "upstox"),
        default="nse",
        help=(
            "Use official NSE by default. Select upstox explicitly only as the documented "
            "regulated-broker fallback when NSE delivery is inaccessible."
        ),
    )
    nse_source = parser.add_mutually_exclusive_group()
    nse_source.add_argument("--nse-file")
    nse_source.add_argument("--nse-url")
    upstox_source = parser.add_mutually_exclusive_group()
    upstox_source.add_argument("--upstox-security-master-file")
    upstox_source.add_argument("--upstox-security-master-url")
    parser.add_argument(
        "--upstox-security-master-approval-reference",
        default=DEFAULT_SOURCE_GOVERNANCE_REFERENCE,
    )
    parser.add_argument("--nse-min-rows", type=int, default=1000)
    parser.add_argument(
        "--financial",
        action="append",
        default=[],
        metavar="SECURITY,FILE,SOURCE_URI[,APPROVAL_REFERENCE]",
        help=(
            "Repeat for each one-security financial CSV export. Non-official sources require "
            "a license/contract/source-governance approval reference."
        ),
    )
    parser.add_argument("--financial-min-rows", type=int, default=5)
    parser.add_argument(
        "--market",
        action="append",
        default=[],
        metavar="SECURITY,PROVIDER,FILE,SOURCE_URI[,APPROVAL_REFERENCE]",
        help=(
            "Repeat for each one-security OHLCV history export. Non-official sources require "
            "a license/contract/source-governance approval reference."
        ),
    )
    parser.add_argument("--market-interval", default="1d")
    parser.add_argument("--market-timezone", default="Asia/Kolkata")
    parser.add_argument("--market-min-rows", type=int, default=30)
    parser.add_argument(
        "--metrics",
        action="append",
        default=[],
        metavar="SECURITY,FILE,SOURCE_URI[,APPROVAL_REFERENCE]",
        help=(
            "Repeat for each one-security comparable-metrics export. Non-official sources "
            "require a license/contract/source-governance approval reference."
        ),
    )
    parser.add_argument("--metrics-min-rows", type=int, default=3)
    parser.add_argument(
        "--benchmark",
        action="append",
        default=[],
        metavar="CODE,FILE,OFFICIAL_SOURCE_URL",
        help=(
            "Repeat for official NSE/NSE Indices benchmark exports. The source URL must be on an "
            "approved official domain."
        ),
    )
    parser.add_argument("--benchmark-interval", default="1d")
    parser.add_argument("--benchmark-timezone", default="Asia/Kolkata")
    parser.add_argument("--benchmark-min-rows", type=int, default=30)
    parser.add_argument(
        "--macro",
        action="append",
        default=[],
        metavar="PROVIDER,...",
        help=(
            "Official macro export. Use RBI,SERIES_KEY,FILE,OFFICIAL_SOURCE_URL or "
            "NSDL,FILE,OFFICIAL_SOURCE_URL."
        ),
    )
    parser.add_argument("--macro-min-rows", type=int, default=1)
    parser.add_argument("--run-official-feeds", action="store_true")
    parser.add_argument("--official-feed-limit", type=int, default=4)
    parser.add_argument("--embed-evidence", action="store_true")
    parser.add_argument("--embedding-limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Return a failing exit code if the final hard data-readiness gate is not satisfied.",
    )
    args = parser.parse_args()

    try:
        financials = tuple(parse_financial_spec(value) for value in args.financial)
        markets = tuple(parse_market_spec(value) for value in args.market)
        metrics = tuple(parse_metrics_spec(value) for value in args.metrics)
        benchmarks = tuple(parse_benchmark_spec(value) for value in args.benchmark)
        macros = tuple(parse_macro_spec(value) for value in args.macro)
        plan = build_bootstrap_plan(
            python_executable=sys.executable,
            scripts_dir=SCRIPTS_DIR,
            skip_nse=args.skip_nse,
            security_master_provider=args.security_master_provider,
            nse_file=Path(args.nse_file) if args.nse_file else None,
            nse_url=args.nse_url,
            upstox_file=(
                Path(args.upstox_security_master_file)
                if args.upstox_security_master_file
                else None
            ),
            upstox_url=args.upstox_security_master_url,
            upstox_approval_reference=args.upstox_security_master_approval_reference,
            nse_min_rows=args.nse_min_rows,
            financials=financials,
            financial_min_rows=args.financial_min_rows,
            markets=markets,
            market_interval=args.market_interval,
            market_timezone=args.market_timezone,
            market_min_rows=args.market_min_rows,
            metrics=metrics,
            metrics_min_rows=args.metrics_min_rows,
            benchmarks=benchmarks,
            benchmark_interval=args.benchmark_interval,
            benchmark_timezone=args.benchmark_timezone,
            benchmark_min_rows=args.benchmark_min_rows,
            macros=macros,
            macro_min_rows=args.macro_min_rows,
            dry_run=args.dry_run,
            run_official_feeds=args.run_official_feeds,
            official_feed_limit=args.official_feed_limit,
            embed_evidence=args.embed_evidence,
            embedding_limit=args.embedding_limit,
        )
    except ValueError as exc:
        parser.error(str(exc))

    settings = get_settings()
    if not args.dry_run and not settings.database_url:
        parser.error("DATABASE_URL must be configured unless --dry-run is used")

    summary: dict[str, object] = {
        "data_policy": "real_provenance_required",
        "security_master_provider": (
            "skipped" if args.skip_nse else args.security_master_provider
        ),
        "dry_run": args.dry_run,
        "stage_count": len(plan),
        "stages": [],
    }
    if settings.database_url:
        summary["coverage_before"] = asyncio.run(_coverage_report())

    stage_results: list[dict[str, Any]] = []
    for stage in plan:
        result = _run_stage(stage.name, stage.command)
        stage_results.append(result)
        summary["stages"] = stage_results
        if not result["ok"]:
            summary["failed_stage"] = stage.name
            if settings.database_url:
                summary["coverage_after"] = asyncio.run(_coverage_report())
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 1

    final_report: dict[str, object] | None = None
    if settings.database_url:
        final_report = asyncio.run(_coverage_report())
        summary["coverage_after"] = final_report

    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    if args.require_ready and (final_report is None or final_report.get("ready") is not True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
