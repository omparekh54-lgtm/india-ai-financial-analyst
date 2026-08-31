from __future__ import annotations

from dataclasses import dataclass
from typing import Self
from urllib.parse import quote

import httpx

from app.connectors.http_fetcher import SourceFetchError

NSE_HOME = "https://www.nseindia.com/"
NSE_EQUITY_PAGE = "https://www.nseindia.com/get-quotes/equity"
NSE_QUOTE_API = "https://www.nseindia.com/api/quote-equity"


@dataclass(frozen=True)
class NseIndustryClassification:
    symbol: str
    isin: str
    macro_sector: str
    sector: str
    industry: str
    basic_industry: str

    @property
    def source_uri(self) -> str:
        return f"{NSE_QUOTE_API}?symbol={quote(self.symbol, safe='')}"

    def as_dict(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "isin": self.isin,
            "macro_sector": self.macro_sector,
            "sector": self.sector,
            "industry": self.industry,
            "basic_industry": self.basic_industry,
            "source_uri": self.source_uri,
        }


class NseIndustryClassificationFetcher:
    """Fetch the official four-level NSE industry classification for listed equities.

    NSE's public quote endpoint commonly requires a browser-style session. The fetcher warms a
    cookie session first and retries once after refreshing the symbol landing page when NSE rejects
    the API request. It deliberately does not implement aggressive concurrency.
    """

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
            raise SourceFetchError("Unable to establish NSE public session") from exc

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def fetch(
        self,
        symbol: str,
        *,
        expected_isin: str | None = None,
    ) -> NseIndustryClassification:
        cleaned_symbol = symbol.strip().upper()
        if not cleaned_symbol:
            raise ValueError("NSE symbol is required")
        if self._client is None:
            await self.start()
        assert self._client is not None

        landing_url = f"{NSE_EQUITY_PAGE}?symbol={quote(cleaned_symbol, safe='')}"
        response = await self._request_quote(cleaned_symbol, referer=landing_url)
        if response.status_code in {401, 403}:
            try:
                landing = await self._client.get(landing_url)
                landing.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceFetchError(
                    f"NSE classification session refresh failed for {cleaned_symbol}"
                ) from exc
            response = await self._request_quote(cleaned_symbol, referer=landing_url)

        if response.status_code in {401, 403}:
            raise SourceFetchError(f"NSE classification session was rejected for {cleaned_symbol}")
        if response.status_code == 429:
            raise SourceFetchError(f"NSE classification rate limit exceeded for {cleaned_symbol}")
        try:
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError(
                f"Invalid NSE classification response for {cleaned_symbol}"
            ) from exc

        return parse_nse_quote_classification(
            payload,
            expected_symbol=cleaned_symbol,
            expected_isin=expected_isin,
        )

    async def _request_quote(self, symbol: str, *, referer: str) -> httpx.Response:
        assert self._client is not None
        try:
            return await self._client.get(
                NSE_QUOTE_API,
                params={"symbol": symbol},
                headers={"Referer": referer},
            )
        except httpx.HTTPError as exc:
            raise SourceFetchError(f"Unable to fetch NSE classification for {symbol}") from exc


def parse_nse_quote_classification(
    payload: object,
    *,
    expected_symbol: str,
    expected_isin: str | None = None,
) -> NseIndustryClassification:
    if not isinstance(payload, dict):
        raise TypeError("NSE quote response must be an object")

    info = payload.get("info")
    industry_info = payload.get("industryInfo")
    if not isinstance(info, dict) or not isinstance(industry_info, dict):
        raise TypeError("NSE quote response info and industryInfo must be objects")

    symbol = _text(info.get("symbol")) or expected_symbol.strip().upper()
    if symbol.upper() != expected_symbol.strip().upper():
        raise ValueError(
            f"NSE classification symbol mismatch: expected {expected_symbol}, received {symbol}"
        )

    isin = _text(info.get("isin"))
    if not isin:
        raise ValueError(f"NSE classification response for {symbol} is missing ISIN")
    if expected_isin and isin.upper() != expected_isin.strip().upper():
        raise ValueError(
            f"NSE classification ISIN mismatch for {symbol}: expected {expected_isin}, received {isin}"
        )

    macro_sector = _required_label(industry_info, "macro", symbol)
    sector = _required_label(industry_info, "sector", symbol)
    industry = _required_label(industry_info, "industry", symbol)
    basic_industry = _required_label(industry_info, "basicIndustry", symbol)

    return NseIndustryClassification(
        symbol=symbol.upper(),
        isin=isin.upper(),
        macro_sector=macro_sector,
        sector=sector,
        industry=industry,
        basic_industry=basic_industry,
    )


def _required_label(payload: dict[object, object], key: str, symbol: str) -> str:
    value = _text(payload.get(key))
    if not value or value.lower() in {"na", "n/a", "none", "null", "-"}:
        raise ValueError(f"NSE classification for {symbol} is missing {key}")
    return value


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
