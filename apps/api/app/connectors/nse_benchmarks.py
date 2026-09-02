from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Self
from zoneinfo import ZoneInfo

import httpx

from app.connectors.http_fetcher import SourceFetchError
from app.ingestion.market import MarketBarInput, normalize_market_bar

NSE_HOME = "https://www.nseindia.com/"
NSE_INDEX_HISTORY_PAGE = "https://www.nseindia.com/reports-indices-historical-index-data"
NSE_VIX_HISTORY_PAGE = "https://www.nseindia.com/reports-indices-historical-vix"

_INDEX_HISTORY_PATHS = (
    "/api/historical/indicesHistory",
    "/api/historicalOR/indicesHistory",
    "/historicalOR/indicesHistory",
)
_VIX_HISTORY_PATHS = (
    "/api/historical/vixhistory",
    "/api/historicalOR/vixhistory",
    "/historicalOR/vixhistory",
)
_BENCHMARK_NAMES = {
    "NIFTY50": "NIFTY 50",
    "INDIAVIX": "INDIA VIX",
}
_INDIA_TZ = ZoneInfo("Asia/Kolkata")


class NseHistoricalBenchmarkFetcher:
    """Fetch official daily NIFTY 50 and India VIX history from NSE public endpoints."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            response = await self._client.get(NSE_HOME)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            await self.close()
            raise SourceFetchError("Unable to establish NSE benchmark session") from exc

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def fetch(
        self,
        benchmark_code: str,
        *,
        from_date: date,
        to_date: date,
    ) -> list[MarketBarInput]:
        code = normalize_benchmark_code(benchmark_code)
        if from_date > to_date:
            raise ValueError("from_date cannot be after to_date")
        if (to_date - from_date).days > 370:
            raise ValueError("NSE benchmark fetch windows cannot exceed 370 days")
        if self._client is None:
            await self.start()
        assert self._client is not None

        if code == "NIFTY50":
            paths = _INDEX_HISTORY_PATHS
            landing_page = NSE_INDEX_HISTORY_PAGE
            params = {
                "indexType": _BENCHMARK_NAMES[code],
                "from": from_date.strftime("%d-%m-%Y"),
                "to": to_date.strftime("%d-%m-%Y"),
            }
        else:
            paths = _VIX_HISTORY_PATHS
            landing_page = NSE_VIX_HISTORY_PAGE
            params = {
                "from": from_date.strftime("%d-%m-%Y"),
                "to": to_date.strftime("%d-%m-%Y"),
            }

        response = await self._request_with_fallback(paths, params=params, referer=landing_page)
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceFetchError(f"Invalid NSE {code} historical JSON response") from exc
        return parse_nse_benchmark_history(payload, benchmark_code=code)

    async def _request_with_fallback(
        self,
        paths: tuple[str, ...],
        *,
        params: dict[str, str],
        referer: str,
    ) -> httpx.Response:
        assert self._client is not None
        last_status: int | None = None
        for path in paths:
            url = f"https://www.nseindia.com{path}"
            response = await self._request(url, params=params, referer=referer)
            if response.status_code in {401, 403}:
                await self._refresh_session(referer)
                response = await self._request(url, params=params, referer=referer)
            if response.status_code == 429:
                raise SourceFetchError("NSE benchmark history rate limit exceeded")
            if response.status_code in {404, 410}:
                last_status = response.status_code
                continue
            if response.status_code in {401, 403}:
                raise SourceFetchError("NSE benchmark history session was rejected")
            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceFetchError("Unable to fetch NSE benchmark history") from exc
            return response
        raise SourceFetchError(
            "No supported NSE benchmark history endpoint was available"
            + (f"; last status={last_status}" if last_status is not None else "")
        )

    async def _refresh_session(self, referer: str) -> None:
        assert self._client is not None
        try:
            response = await self._client.get(referer)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to refresh NSE benchmark session") from exc

    async def _request(
        self,
        url: str,
        *,
        params: dict[str, str],
        referer: str,
    ) -> httpx.Response:
        assert self._client is not None
        try:
            return await self._client.get(url, params=params, headers={"Referer": referer})
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch NSE benchmark history") from exc


def normalize_benchmark_code(value: str) -> str:
    normalized = " ".join(value.strip().upper().split())
    aliases = {
        "NIFTY50": "NIFTY50",
        "NIFTY 50": "NIFTY50",
        "INDIAVIX": "INDIAVIX",
        "INDIA VIX": "INDIAVIX",
    }
    code = aliases.get(normalized)
    if code is None:
        raise ValueError("benchmark_code must be NIFTY50 or INDIAVIX")
    return code


def parse_nse_benchmark_history(
    payload: object,
    *,
    benchmark_code: str,
) -> list[MarketBarInput]:
    code = normalize_benchmark_code(benchmark_code)
    rows, turnover_by_date = _extract_rows(payload, code=code)
    bars: dict[date, MarketBarInput] = {}
    expected_name = _BENCHMARK_NAMES[code]

    for row in rows:
        row_name = _text(row.get("EOD_INDEX_NAME"))
        if row_name and " ".join(row_name.upper().split()) != expected_name:
            raise ValueError(
                f"NSE benchmark response name mismatch: expected {expected_name}, received {row_name}"
            )
        session_date = _parse_date(row.get("EOD_TIMESTAMP") or row.get("TIMESTAMP"))
        volume = _number(row.get("HIT_TRADED_QTY"))
        if volume is None:
            volume = turnover_by_date.get(session_date)
        bar = normalize_market_bar(
            MarketBarInput(
                ts=datetime.combine(session_date, time(15, 30), tzinfo=_INDIA_TZ).astimezone(UTC),
                open=_required_number(row, "EOD_OPEN_INDEX_VAL"),
                high=_required_number(row, "EOD_HIGH_INDEX_VAL"),
                low=_required_number(row, "EOD_LOW_INDEX_VAL"),
                close=_required_number(row, "EOD_CLOSE_INDEX_VAL"),
                volume=volume,
                provider="nse",
                interval="1d",
                is_adjusted=False,
            )
        )
        bars[session_date] = bar

    if not bars:
        raise ValueError(f"NSE {code} history response contained no daily bars")
    return [bars[key] for key in sorted(bars)]


def _extract_rows(
    payload: object,
    *,
    code: str,
) -> tuple[list[dict[str, object]], dict[date, float]]:
    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
        return rows, {}
    if not isinstance(payload, dict):
        raise TypeError("NSE benchmark history response must be an object or array")

    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)], {}
    if not isinstance(data, dict):
        if code == "INDIAVIX" and isinstance(payload.get("vixHistory"), list):
            rows = payload["vixHistory"]
            return [row for row in rows if isinstance(row, dict)], {}
        raise TypeError("NSE benchmark history response is missing data")

    if code == "NIFTY50":
        raw_rows = data.get("indexCloseOnlineRecords") or data.get("indexCloseRecords") or []
        turnover_rows = data.get("indexTurnoverRecords") or []
    else:
        raw_rows = data.get("vixHistory") or data.get("indexCloseOnlineRecords") or []
        turnover_rows = []
    if not isinstance(raw_rows, list) or not isinstance(turnover_rows, list):
        raise TypeError("NSE benchmark history data has an invalid row structure")

    turnover_by_date: dict[date, float] = {}
    for row in turnover_rows:
        if not isinstance(row, dict):
            continue
        try:
            session_date = _parse_date(row.get("HIT_TIMESTAMP") or row.get("EOD_TIMESTAMP"))
        except (TypeError, ValueError):
            continue
        volume = _number(row.get("HIT_TRADED_QTY"))
        if volume is not None:
            turnover_by_date[session_date] = volume
    return [row for row in raw_rows if isinstance(row, dict)], turnover_by_date


def _parse_date(value: object) -> date:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NSE benchmark row is missing its date")
    cleaned = value.strip().upper()
    for pattern in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%y"):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported NSE benchmark date: {value}")


def _required_number(row: dict[str, object], key: str) -> float:
    value = _number(row.get(key))
    if value is None:
        raise ValueError(f"NSE benchmark row is missing numeric {key}")
    return value


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if not cleaned or cleaned in {"-", "--"}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
