"""Bounded collection using existing adapters, with validation before each import."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.connectors.nse_sectoral_indices import (
    NSE_NIFTY50_INDEX_CSV,
    NseSectoralIndexFetcher,
)
from app.core.config import get_settings
from app.core.data_readiness import load_data_coverage
from app.core.prepared_security_readiness import persist_prepared_security_readiness
from app.core.security_readiness import evaluate_security_readiness, load_security_agent_coverage
from app.db import create_database_engine
from app.ingestion.reference_provenance import resolve_security

API_ROOT = Path(__file__).resolve().parents[1]


def run_import(command: list[str], *, timeout: int) -> dict[str, Any]:
    """Retain structured importer results; never publish connection tracebacks/secrets."""
    try:
        result = subprocess.run(
            [sys.executable, *command], cwd=API_ROOT, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "import_timeout", "timeout_seconds": timeout}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        error_types = re.findall(
            r"^([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception)):", result.stderr, re.MULTILINE,
        )
        payload = {"reason": "importer_did_not_return_json",
                   "error_type": error_types[-1] if error_types else None}
    return {"ok": result.returncode == 0, "exit_code": result.returncode, "result": payload}


def collect_financial_symbol(symbol: str, *, metrics: bool = False) -> dict[str, Any]:
    command = (
        ["scripts/backfill_derived_security_metrics.py", "--security", symbol, "--min-metrics", "3"]
        if metrics else [
            "scripts/backfill_nse_financial_results.py", "--security", symbol,
            "--max-periods", "10", "--document-delay-seconds", "0.2",
            "--collect-available-history",
        ]
    )
    command.append("--skip-coverage-snapshot")
    validation = run_import([*command, "--dry-run"], timeout=180)
    if not validation["ok"]:
        return {"symbol": symbol, "ok": False, "validation": validation, "import": None}
    imported = run_import(command, timeout=360)
    return {"symbol": symbol, "ok": imported["ok"], "validation": validation, "import": imported}


async def collect(mode: str, *, limit: int, after_symbol: str | None) -> int:
    universe = await NseSectoralIndexFetcher(
        source_url=NSE_NIFTY50_INDEX_CSV, index_name="NIFTY 50",
    ).fetch()
    symbols = sorted({entry.symbol for entry in universe.entries})
    if len(symbols) != 50:
        raise RuntimeError("Official NIFTY 50 universe must contain exactly 50 distinct symbols")
    selected = [symbol for symbol in symbols if not after_symbol or symbol > after_symbol][:limit]
    if mode == "financials":
        selected = await incomplete_financial_symbols(selected)
    failures = 0
    processed: list[str] = []
    consecutive_source_failures = 0
    if mode in {"financials", "metrics"}:
        for symbol in selected:
            result = collect_financial_symbol(symbol, metrics=mode == "metrics")
            failures += not result["ok"]
            processed.append(symbol)
            print(json.dumps(result, sort_keys=True), flush=True)
            validation = result["validation"]
            payload = validation.get("result", {})
            source_failed = (
                validation.get("reason") == "import_timeout"
                or (result.get("import") or {}).get("reason") == "import_timeout"
                or payload.get("error_type") == "SourceFetchError"
                or any(item.get("error_type") == "SourceFetchError"
                       for item in payload.get("results", []))
                or any("free-tier storage guard" in str(item.get("error", ""))
                       for item in (result.get("import") or {}).get(
                           "result", {}).get("results", []))
            )
            consecutive_source_failures = consecutive_source_failures + 1 if source_failed else 0
            if consecutive_source_failures >= 3:
                print(json.dumps({"status": "source_unavailable",
                                  "unprocessed_count": len(selected) - len(processed),
                                  "next_after_symbol": symbol}), flush=True)
                break
            await asyncio.sleep(0.5)
    elif mode == "market":
        command = [
            "scripts/backfill_yfinance_market_history.py", "--lookback-days", "365",
            "--interval", "1d", "--request-delay-seconds", "0.5",
            "--confirm-yahoo-research-use",
        ]
        for symbol in selected:
            command.extend(["--security", symbol])
        if selected:
            validation = run_import([*command, "--dry-run"], timeout=180)
            imported = run_import(command, timeout=1200) if validation["ok"] else None
            failures = int(imported is None or not imported["ok"])
            processed.extend(selected)
            print(json.dumps({"validation": validation, "import": imported}, sort_keys=True))
    else:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL must be configured")
        engine = create_database_engine(settings.database_url)
        try:
            corpus_coverage = await load_data_coverage(engine)
            for symbol in selected:
                security_id, _ = await resolve_security(engine, symbol)
                coverage, resolved_symbol = await load_security_agent_coverage(engine, security_id)
                readiness = evaluate_security_readiness(
                    security_id, resolved_symbol, coverage, corpus_coverage, settings,
                )
                await persist_prepared_security_readiness(engine, readiness)
                processed.append(symbol)
                failures += not readiness.ready
                print(json.dumps(readiness.as_dict(), sort_keys=True), flush=True)
        finally:
            await engine.dispose()
    print(json.dumps({
        "mode": mode, "target_count": len(selected), "failure_count": failures,
        "processed_count": len(processed), "unprocessed_count": len(selected) - len(processed),
        "next_after_symbol": processed[-1] if processed else after_symbol,
        "status": "completed" if not failures else "completed_with_gaps",
    }, sort_keys=True), flush=True)
    return int(failures > 0)


async def incomplete_financial_symbols(symbols: list[str]) -> list[str]:
    """Resume from live financial, filing and earnings contracts, rather than a cursor alone."""
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    incomplete = []
    try:
        for symbol in symbols:
            security_id, _ = await resolve_security(engine, symbol)
            coverage, _ = await load_security_agent_coverage(engine, security_id)
            if not (
                coverage.financial_history_securities
                and coverage.recent_filing_evidence_securities
                and coverage.recent_earnings_evidence_securities
            ):
                incomplete.append(symbol)
            else:
                print(json.dumps({"symbol": symbol, "status": "already_complete"}), flush=True)
    finally:
        await engine.dispose()
    return incomplete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("financials", "market", "metrics", "readiness"))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--after-symbol")
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        parser.error("--limit must be between 1 and 50")
    return asyncio.run(collect(
        args.mode, limit=args.limit,
        after_symbol=args.after_symbol.upper() if args.after_symbol else None,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
