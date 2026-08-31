from __future__ import annotations

from datetime import date, datetime
from urllib.parse import quote

import httpx

from app.ingestion.market import MarketBarInput, normalize_market_bar

UPSTOX_HISTORY_BASE_URL = "https://api.upstox.com/v3/historical-candle"


class UpstoxHistoricalDataError(RuntimeError):
    """Raised when Upstox historical data cannot be safely normalized."""


class UpstoxHistoricalClient:
    """Bounded client for real Upstox V3 historical daily candles."""

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = UPSTOX_HISTORY_BASE_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("Upstox historical access token cannot be empty")
        self._access_token = token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def request_url(self, instrument_key: str, *, from_date: date, to_date: date) -> str:
        key = instrument_key.strip()
        if not key:
            raise ValueError("instrument_key cannot be empty")
        if from_date > to_date:
            raise ValueError("from_date cannot be after to_date")
        encoded_key = quote(key, safe="")
        return (
            f"{self.base_url}/{encoded_key}/days/1/"
            f"{to_date.isoformat()}/{from_date.isoformat()}"
        )

    async def fetch_daily(
        self,
        instrument_key: str,
        *,
        from_date: date,
        to_date: date,
    ) -> tuple[str, list[MarketBarInput]]:
        url = self.request_url(instrument_key, from_date=from_date, to_date=to_date)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(url, headers=headers)
        if response.status_code == 429:
            raise UpstoxHistoricalDataError("Upstox historical-data rate limit reached")
        if response.status_code in {401, 403}:
            raise UpstoxHistoricalDataError("Upstox historical-data credential was rejected")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise UpstoxHistoricalDataError(
                f"Upstox historical-data request failed with HTTP {response.status_code}"
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstoxHistoricalDataError("Upstox historical-data response is not JSON") from exc
        return url, parse_daily_candles(payload)


def parse_daily_candles(payload: object) -> list[MarketBarInput]:
    if not isinstance(payload, dict):
        raise UpstoxHistoricalDataError("Upstox historical-data payload must be an object")
    if payload.get("status") != "success":
        raise UpstoxHistoricalDataError("Upstox historical-data payload did not report success")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise UpstoxHistoricalDataError("Upstox historical-data payload is missing data")
    candles = data.get("candles")
    if not isinstance(candles, list):
        raise UpstoxHistoricalDataError("Upstox historical-data payload is missing candles")

    normalized: list[MarketBarInput] = []
    seen: set[datetime] = set()
    for index, candle in enumerate(candles):
        if not isinstance(candle, list) or len(candle) < 6:
            raise UpstoxHistoricalDataError(
                f"Upstox candle {index} must contain timestamp/OHLC/volume"
            )
        try:
            timestamp = datetime.fromisoformat(str(candle[0]))
            bar = normalize_market_bar(
                MarketBarInput(
                    ts=timestamp,
                    open=float(candle[1]),
                    high=float(candle[2]),
                    low=float(candle[3]),
                    close=float(candle[4]),
                    volume=float(candle[5]),
                    provider="upstox",
                    interval="1d",
                    is_adjusted=False,
                )
            )
        except (TypeError, ValueError) as exc:
            raise UpstoxHistoricalDataError(f"invalid Upstox candle at index {index}: {exc}") from exc
        if bar.ts in seen:
            raise UpstoxHistoricalDataError(
                f"duplicate Upstox candle timestamp: {bar.ts.isoformat()}"
            )
        seen.add(bar.ts)
        normalized.append(bar)

    return sorted(normalized, key=lambda bar: bar.ts)
