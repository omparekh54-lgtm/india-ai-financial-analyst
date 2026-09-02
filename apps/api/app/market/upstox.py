from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.market.contracts import MarketBar, MarketQuote


class UpstoxMarketDataError(RuntimeError):
    pass


class UpstoxMarketDataAdapter:
    """Read-only Upstox adapter for exchange quotes and V3 historical candles.

    The adapter deliberately accepts an access token at runtime instead of reading or persisting
    broker credentials itself. OAuth/token storage belongs to a separate authenticated service.
    """

    name = "upstox"

    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = "https://api.upstox.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        token = access_token.strip()
        if not token:
            raise ValueError("Upstox access token cannot be empty")
        self.access_token = token
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    async def quote(self, instrument_id: str) -> MarketQuote:
        instrument_key = _instrument_key(instrument_id)
        payload = await self._get_json(
            "/v2/market-quote/quotes",
            params={"instrument_key": instrument_key},
        )
        quote_data = _single_quote(payload, instrument_key)

        symbol = _required_string(quote_data, "symbol")
        timestamp = _parse_timestamp(quote_data.get("timestamp"))
        depth = quote_data.get("depth") if isinstance(quote_data.get("depth"), dict) else {}
        bid = _best_depth_price(depth.get("buy"))
        ask = _best_depth_price(depth.get("sell"))

        return MarketQuote(
            symbol=symbol,
            provider=self.name,
            exchange=_exchange_from_instrument_key(instrument_key),
            last_price=_required_float(quote_data, "last_price"),
            timestamp=timestamp,
            bid=bid,
            ask=ask,
            volume=_optional_float(quote_data.get("volume")),
            is_delayed=False,
            metadata={
                "instrument_key": instrument_key,
                "instrument_token": quote_data.get("instrument_token"),
                "last_trade_time": quote_data.get("last_trade_time"),
                "ohlc": quote_data.get("ohlc") if isinstance(quote_data.get("ohlc"), dict) else {},
                "average_price": quote_data.get("average_price"),
                "net_change": quote_data.get("net_change"),
                "open_interest": quote_data.get("oi"),
                "lower_circuit_limit": quote_data.get("lower_circuit_limit"),
                "upper_circuit_limit": quote_data.get("upper_circuit_limit"),
                "exchange_snapshot": True,
            },
        )

    async def history(
        self,
        instrument_id: str,
        *,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketBar]:
        if end < start:
            raise ValueError("history end must be on or after start")
        instrument_key = _instrument_key(instrument_id)
        unit, upstox_interval = _history_interval(interval)
        encoded_key = quote(instrument_key, safe="")
        path = (
            f"/v3/historical-candle/{encoded_key}/{unit}/{upstox_interval}/"
            f"{end.date().isoformat()}/{start.date().isoformat()}"
        )
        payload = await self._get_json(path)
        data = payload.get("data") if isinstance(payload, dict) else None
        candles = data.get("candles") if isinstance(data, dict) else None
        if not isinstance(candles, list):
            raise UpstoxMarketDataError("Upstox historical response did not contain candles")

        bars: list[MarketBar] = []
        for candle in candles:
            if not isinstance(candle, list) or len(candle) < 6:
                raise UpstoxMarketDataError("Upstox returned a malformed historical candle")
            timestamp = _parse_timestamp(candle[0])
            bars.append(
                MarketBar(
                    symbol=instrument_key,
                    provider=self.name,
                    exchange=_exchange_from_instrument_key(instrument_key),
                    interval=interval,
                    timestamp=timestamp,
                    open=_number(candle[1], "open"),
                    high=_number(candle[2], "high"),
                    low=_number(candle[3], "low"),
                    close=_number(candle[4], "close"),
                    volume=_optional_float(candle[5]),
                    is_adjusted=False,
                )
            )
        bars.sort(key=lambda item: item.timestamp)
        return bars

    async def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(15.0, connect=5.0),
                transport=self.transport,
            ) as client:
                response = await client.get(path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise UpstoxMarketDataError("Upstox market-data request failed") from exc

        if response.status_code in {401, 403}:
            raise UpstoxMarketDataError("Upstox access token is invalid or expired")
        if response.status_code == 429:
            raise UpstoxMarketDataError("Upstox market-data rate limit exceeded")
        if response.status_code >= 400:
            raise UpstoxMarketDataError(
                f"Upstox market-data request returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstoxMarketDataError("Upstox returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise UpstoxMarketDataError("Upstox market-data response was not successful")
        return payload


def _instrument_key(value: str) -> str:
    candidate = value.strip()
    if not candidate or "|" not in candidate:
        raise ValueError("Upstox instrument_id must be an instrument key such as NSE_EQ|<ISIN>")
    return candidate


def _exchange_from_instrument_key(instrument_key: str) -> str:
    prefix = instrument_key.split("|", 1)[0].upper()
    exchange = prefix.split("_", 1)[0]
    return exchange or "UNKNOWN"


def _single_quote(payload: dict[str, Any], instrument_key: str) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict) or not data:
        raise UpstoxMarketDataError("Upstox quote response did not contain market data")
    for item in data.values():
        if not isinstance(item, dict):
            continue
        if str(item.get("instrument_token") or "") == instrument_key:
            return item
    if len(data) == 1:
        only = next(iter(data.values()))
        if isinstance(only, dict):
            return only
    raise UpstoxMarketDataError("Upstox quote response did not contain the requested instrument")


def _best_depth_price(value: object) -> float | None:
    if not isinstance(value, list):
        return None
    for level in value:
        if not isinstance(level, dict):
            continue
        price = _optional_float(level.get("price"))
        if price is not None and price > 0:
            return price
    return None


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if value is None or not str(value).strip():
        raise UpstoxMarketDataError(f"Upstox quote is missing {key}")
    return str(value).strip()


def _required_float(data: dict[str, Any], key: str) -> float:
    value = _optional_float(data.get(key))
    if value is None:
        raise UpstoxMarketDataError(f"Upstox quote is missing numeric {key}")
    return value


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: object, label: str) -> float:
    result = _optional_float(value)
    if result is None:
        raise UpstoxMarketDataError(f"Upstox candle has invalid {label}")
    return result


def _parse_timestamp(value: object) -> datetime:
    if value is None:
        raise UpstoxMarketDataError("Upstox market data is missing a timestamp")
    candidate = str(value).strip()
    if not candidate:
        raise UpstoxMarketDataError("Upstox market data has an empty timestamp")
    if candidate.isdigit():
        seconds: float = float(candidate)
        if seconds > 10_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=UTC)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise UpstoxMarketDataError("Upstox market data has an invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise UpstoxMarketDataError("Upstox timestamp must include timezone information")
    return parsed


def _history_interval(value: str) -> tuple[str, str]:
    normalized = value.strip().lower()
    aliases = {
        "1d": ("days", "1"),
        "1day": ("days", "1"),
        "1w": ("weeks", "1"),
        "1week": ("weeks", "1"),
        "1mo": ("months", "1"),
        "1month": ("months", "1"),
    }
    if normalized in aliases:
        return aliases[normalized]

    minute_match = re.fullmatch(r"(\d{1,3})(?:m|min|mins|minute|minutes)", normalized)
    if minute_match:
        interval = int(minute_match.group(1))
        if 1 <= interval <= 300:
            return "minutes", str(interval)

    hour_match = re.fullmatch(r"(\d)(?:h|hr|hrs|hour|hours)", normalized)
    if hour_match:
        interval = int(hour_match.group(1))
        if 1 <= interval <= 5:
            return "hours", str(interval)

    raise ValueError(
        "Unsupported Upstox interval. Use 1-300m, 1-5h, 1d, 1w, or 1mo."
    )
