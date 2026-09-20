from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from app.connectors.yahoo_finance import YahooFinanceDataError, parse_history_frame
from app.ingestion.market import MarketBarInput

YAHOO_FINANCE_QUOTE_URL = "https://finance.yahoo.com/quote"

# NSE's own historical-index API (apps.nseindia.com / www.nseindia.com/api/historical/...) is
# geo-fenced to Indian IPs: it returns NSE's "This site is not accessible in your region at the
# moment" page (not a 403) to any datacenter/cloud IP, including GitHub Actions runners, no
# matter how the session/cookies/headers are set up. That's structurally unfixable from CI.
# Yahoo Finance is not geo-fenced and already backs this project's per-security price history
# (see app/connectors/yahoo_finance.py), so benchmarks are fetched from Yahoo too, using the
# same "restricted external source, internal-research-only" provenance labeling already used
# for Yahoo-sourced security bars.
_BENCHMARK_YAHOO_SYMBOLS = {
    "NIFTY50": "^NSEI",
    "INDIAVIX": "^INDIAVIX",
}


def yahoo_benchmark_symbol(benchmark_code: str) -> str:
    symbol = _BENCHMARK_YAHOO_SYMBOLS.get(benchmark_code.strip().upper())
    if symbol is None:
        raise ValueError(f"no Yahoo Finance symbol is mapped for benchmark code: {benchmark_code}")
    return symbol


def benchmark_history_source_url(benchmark_code: str) -> str:
    symbol = yahoo_benchmark_symbol(benchmark_code)
    return f"{YAHOO_FINANCE_QUOTE_URL}/{quote(symbol, safe='')}/history/"


@dataclass(frozen=True)
class YahooBenchmarkHistoryResult:
    yahoo_symbol: str
    source_url: str
    response_sha256: str
    bars: tuple[MarketBarInput, ...]


class YahooFinanceBenchmarkHistoryClient:
    """Bounded Yahoo Finance importer for NIFTY 50 / India VIX daily history."""

    def __init__(
        self,
        *,
        loader: Callable[..., Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("timeout_seconds must be between 0 and 120")
        self._loader = loader
        self.timeout_seconds = timeout_seconds

    async def fetch_history(
        self,
        benchmark_code: str,
        *,
        from_date: date,
        to_date: date,
    ) -> YahooBenchmarkHistoryResult:
        if from_date > to_date:
            raise ValueError("from_date cannot be after to_date")

        provider_symbol = yahoo_benchmark_symbol(benchmark_code)
        frame = await asyncio.to_thread(self._download, provider_symbol, from_date, to_date)
        bars = parse_history_frame(frame, interval="1d")
        if not bars:
            raise YahooFinanceDataError(f"Yahoo Finance returned no bars for {provider_symbol}")
        canonical = [
            {
                "ts": bar.ts.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "interval": bar.interval,
            }
            for bar in bars
        ]
        checksum = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return YahooBenchmarkHistoryResult(
            yahoo_symbol=provider_symbol,
            source_url=benchmark_history_source_url(benchmark_code),
            response_sha256=checksum,
            bars=tuple(bars),
        )

    def _download(self, symbol: str, from_date: date, to_date: date) -> Any:
        loader = self._loader
        if loader is None:
            try:
                import yfinance as yf
            except ImportError as exc:
                raise YahooFinanceDataError(
                    "yfinance is not installed; install the market_imports extra"
                ) from exc
            loader = yf.download
        try:
            return loader(
                tickers=symbol,
                start=from_date.isoformat(),
                end=(to_date + timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=False,
                timeout=self.timeout_seconds,
                multi_level_index=False,
            )
        except Exception as exc:
            raise YahooFinanceDataError(
                f"Yahoo Finance history request failed: {type(exc).__name__}"
            ) from exc
