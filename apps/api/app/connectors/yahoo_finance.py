from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from app.ingestion.market import MarketBarInput, normalize_market_bar

YAHOO_FINANCE_QUOTE_URL = "https://finance.yahoo.com/quote"
_SUPPORTED_INTERVALS = frozenset({"1d", "1h", "30m", "15m", "5m", "2m", "1m"})


class YahooFinanceDataError(RuntimeError):
    """Raised when Yahoo Finance data cannot be safely normalized."""


@dataclass(frozen=True)
class YahooFinanceHistoryResult:
    yahoo_symbol: str
    source_url: str
    response_sha256: str
    bars: tuple[MarketBarInput, ...]


def yahoo_symbol(symbol: str, exchange: str) -> str:
    cleaned_symbol = symbol.strip().upper()
    cleaned_exchange = exchange.strip().upper()
    if not cleaned_symbol:
        raise ValueError("symbol cannot be empty")
    if cleaned_symbol.endswith((".NS", ".BO")):
        return cleaned_symbol
    suffix = {"NSE": ".NS", "BSE": ".BO"}.get(cleaned_exchange)
    if suffix is None:
        raise ValueError("Yahoo Finance imports support NSE and BSE securities only")
    return f"{cleaned_symbol}{suffix}"


def history_source_url(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("Yahoo Finance symbol cannot be empty")
    return f"{YAHOO_FINANCE_QUOTE_URL}/{quote(cleaned, safe='.')}/history/"


class YahooFinanceHistoryClient:
    """Bounded Yahoo Finance importer for delayed internal-research data."""

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
        symbol: str,
        *,
        exchange: str,
        from_date: date,
        to_date: date,
        interval: str = "1d",
    ) -> YahooFinanceHistoryResult:
        if from_date > to_date:
            raise ValueError("from_date cannot be after to_date")
        normalized_interval = interval.strip().lower()
        if normalized_interval not in _SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported Yahoo Finance interval: {interval}")
        if normalized_interval != "1d" and (to_date - from_date).days > 59:
            raise ValueError("intraday Yahoo Finance imports are limited to 60 calendar days")

        provider_symbol = yahoo_symbol(symbol, exchange)
        frame = await asyncio.to_thread(
            self._download,
            provider_symbol,
            from_date,
            to_date,
            normalized_interval,
        )
        bars = parse_history_frame(frame, interval=normalized_interval)
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
        return YahooFinanceHistoryResult(
            yahoo_symbol=provider_symbol,
            source_url=history_source_url(provider_symbol),
            response_sha256=checksum,
            bars=tuple(bars),
        )

    def _download(
        self,
        symbol: str,
        from_date: date,
        to_date: date,
        interval: str,
    ) -> Any:
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
                interval=interval,
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


def parse_history_frame(frame: Any, *, interval: str) -> list[MarketBarInput]:
    if frame is None or not hasattr(frame, "iterrows") or not hasattr(frame, "empty"):
        raise YahooFinanceDataError("Yahoo Finance history response is not a data frame")
    if bool(frame.empty):
        return []

    bars: list[MarketBarInput] = []
    seen = set()
    for index, row in frame.iterrows():
        try:
            timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
            if getattr(timestamp, "tzinfo", None) is None:
                if not hasattr(index, "tz_localize"):
                    raise ValueError("timestamp is not timezone-aware")
                timestamp = index.tz_localize("Asia/Kolkata").to_pydatetime()
            bar = normalize_market_bar(
                MarketBarInput(
                    ts=timestamp,
                    open=_required_number(row, "Open"),
                    high=_required_number(row, "High"),
                    low=_required_number(row, "Low"),
                    close=_required_number(row, "Close"),
                    volume=_optional_number(row, "Volume"),
                    provider="yfinance",
                    interval=interval,
                    is_adjusted=False,
                )
            )
        except (TypeError, ValueError) as exc:
            raise YahooFinanceDataError(f"invalid Yahoo Finance row at {index}: {exc}") from exc
        if bar.ts in seen:
            raise YahooFinanceDataError(f"duplicate Yahoo Finance timestamp: {bar.ts.isoformat()}")
        seen.add(bar.ts)
        bars.append(bar)
    return sorted(bars, key=lambda bar: bar.ts)


def _required_number(row: Any, name: str) -> float:
    value = _row_value(row, name)
    number = float(value)
    if math.isnan(number):
        raise ValueError(f"{name} cannot be NaN")
    return number


def _optional_number(row: Any, name: str) -> float | None:
    value = _row_value(row, name)
    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) else number


def _row_value(row: Any, name: str) -> Any:
    if name in row:
        return row[name]
    for key in row.index:
        if isinstance(key, tuple) and name in key:
            return row[key]
    if name == "Volume":
        return None
    raise ValueError(f"Yahoo Finance response is missing {name}")
