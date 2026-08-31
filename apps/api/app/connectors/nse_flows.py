from __future__ import annotations

from datetime import UTC, datetime
from typing import Self

import httpx

from app.connectors.http_fetcher import SourceFetchError
from app.ingestion.macro import MacroObservation

NSE_FII_DII_PAGE = "https://www.nseindia.com/reports/fii-dii"
NSE_FII_DII_API = "https://www.nseindia.com/api/fiidiiTradeReact"


class NseFiiDiiCashFlowFetcher:
    """Fetch the official provisional FII/FPI and DII capital-market activity from NSE."""

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
                "Referer": NSE_FII_DII_PAGE,
            },
        )
        try:
            landing = await self._client.get(NSE_FII_DII_PAGE)
            landing.raise_for_status()
        except httpx.HTTPError as exc:
            await self.close()
            raise SourceFetchError("Unable to establish NSE FII/DII session") from exc

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def fetch(self) -> list[MacroObservation]:
        if self._client is None:
            await self.start()
        assert self._client is not None

        response = await self._request()
        if response.status_code in {401, 403}:
            try:
                landing = await self._client.get(NSE_FII_DII_PAGE)
                landing.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceFetchError("Unable to refresh NSE FII/DII session") from exc
            response = await self._request()
        if response.status_code in {401, 403}:
            raise SourceFetchError("NSE FII/DII session was rejected")
        if response.status_code == 429:
            raise SourceFetchError("NSE FII/DII rate limit exceeded")
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError("Invalid NSE FII/DII response") from exc
        return parse_nse_fii_dii_cash_flows(payload)

    async def _request(self) -> httpx.Response:
        assert self._client is not None
        try:
            return await self._client.get(NSE_FII_DII_API)
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch NSE FII/DII activity") from exc


def parse_nse_fii_dii_cash_flows(payload: object) -> list[MacroObservation]:
    if isinstance(payload, dict):
        rows = payload.get("data")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise TypeError("NSE FII/DII response must contain a row list")

    parsed: dict[str, MacroObservation] = {}
    dates = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        category = _text(raw.get("category")).upper()
        if category not in {"FII/FPI", "DII"}:
            continue
        observation_date = _parse_date(raw.get("date"))
        buy_value = _required_number(raw, "buyValue", category)
        sell_value = _required_number(raw, "sellValue", category)
        net_value = _required_number(raw, "netValue", category)
        if abs((buy_value - sell_value) - net_value) > 0.05:
            raise ValueError(
                f"NSE {category} net value does not reconcile with buy minus sell"
            )
        dates.add(observation_date)
        series_key = "fii_cash_net_cr" if category == "FII/FPI" else "dii_cash_net_cr"
        if series_key in parsed:
            raise ValueError(f"NSE FII/DII response contains duplicate {category} rows")
        parsed[series_key] = MacroObservation(
            series_key=series_key,
            observation_date=observation_date,
            value=net_value,
            unit="INR cr",
            metadata={
                "source": "NSE",
                "source_page": NSE_FII_DII_PAGE,
                "source_endpoint": NSE_FII_DII_API,
                "category": category,
                "market_segment": "capital_market",
                "provisional": True,
                "buy_value_cr": buy_value,
                "sell_value_cr": sell_value,
            },
        )

    required = {"fii_cash_net_cr", "dii_cash_net_cr"}
    if set(parsed) != required:
        missing = ", ".join(sorted(required - set(parsed)))
        raise ValueError(f"NSE FII/DII response is missing required flow series: {missing}")
    if len(dates) != 1:
        raise ValueError("NSE FII and DII rows must have the same reporting date")
    return [parsed["fii_cash_net_cr"], parsed["dii_cash_net_cr"]]


def _parse_date(value: object):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("NSE FII/DII row is missing its date")
    cleaned = value.strip().upper()
    for pattern in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported NSE FII/DII date: {value}")


def _required_number(row: dict[object, object], key: str, category: str) -> float:
    value = _number(row.get(key))
    if value is None:
        raise ValueError(f"NSE {category} row is missing numeric {key}")
    return value


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = f"-{cleaned[1:-1]}"
        if not cleaned or cleaned in {"-", "--"}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
