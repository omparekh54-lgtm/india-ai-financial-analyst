"""Check, read-only, whether this machine can reach the NSE sources the research worker needs.

Run this on the computer that will host the research worker before starting it. It makes a
handful of slow, polite requests, writes nothing to any database and needs no credentials.

    python scripts/check_nse_reachability.py            # checks TCS
    python scripts/check_nse_reachability.py --symbol INFY
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import httpx

from app.connectors.nse_classification import NseIndustryClassificationFetcher
from app.connectors.nse_financial_results import NseFinancialResultsFetcher
from app.ingestion.nse_bhavcopy import bhavcopy_url

TIMEOUT_SECONDS = 60.0


async def check_classification(symbol: str) -> str:
    async with NseIndustryClassificationFetcher() as fetcher:
        result = await fetcher.fetch(symbol)
    return f"{result.sector} / {result.industry} / {result.basic_industry}"


async def check_financial_results(symbol: str) -> str:
    async with NseFinancialResultsFetcher() as fetcher:
        records = await fetcher.fetch_history(symbol)
    return f"{len(records)} result filings listed"


async def check_archives(_: str) -> str:
    day = datetime.now(UTC).date() - timedelta(days=1)
    tried: list[str] = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": "Mozilla/5.0 (research reachability check)"},
        follow_redirects=True,
    ) as client:
        for _attempt in range(7):
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            response = await client.head(bhavcopy_url(day))
            tried.append(f"{day.isoformat()}:{response.status_code}")
            if response.status_code == 200:
                return f"bhavcopy for {day.isoformat()} available"
            day -= timedelta(days=1)
    raise RuntimeError("no recent bhavcopy reachable (" + ", ".join(tried) + ")")


CHECKS: tuple[tuple[str, bool, Callable[[str], Awaitable[str]]], ...] = (
    ("NSE quote API (four-tier classification)", True, check_classification),
    ("NSE financial-results API (XBRL filings)", True, check_financial_results),
    ("NSE archives (daily bhavcopy)", False, check_archives),
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--symbol", default="TCS")
    args = parser.parse_args()
    symbol = args.symbol.strip().upper()

    results = []
    for label, required, check in CHECKS:
        started = datetime.now(UTC)
        try:
            detail = await asyncio.wait_for(check(symbol), timeout=TIMEOUT_SECONDS)
            ok = True
        except Exception as exc:  # noqa: BLE001 - every failure is reported, none is fatal
            detail = f"{type(exc).__name__}: {exc}"[:200] or type(exc).__name__
            ok = False
        seconds = round((datetime.now(UTC) - started).total_seconds(), 1)
        results.append({"check": label, "ok": ok, "required": required, "seconds": seconds})
        print(f"[{'OK  ' if ok else 'FAIL'}] {label} ({seconds}s): {detail}", flush=True)
        await asyncio.sleep(1.0)

    required_ok = all(item["ok"] for item in results if item["required"])
    print(
        json.dumps(
            {
                "checked_at": datetime.now(UTC).isoformat(),
                "symbol": symbol,
                "worker_can_fetch_on_demand": required_ok,
                "results": results,
            },
            indent=2,
        )
    )
    if not required_ok:
        print(
            "\nThis machine cannot reach the NSE APIs the on-demand fetch needs. Run the worker "
            "from another Indian connection, or ask the owner about CLASSIFICATION_POLICY.",
            file=sys.stderr,
        )
    return 0 if required_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

