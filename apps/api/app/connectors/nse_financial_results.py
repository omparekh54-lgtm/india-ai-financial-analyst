from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Self
from urllib.parse import urljoin, urlparse

import httpx

from app.connectors.http_fetcher import SourceFetchError

NSE_FINANCIAL_RESULTS_PAGE = (
    "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"
)
NSE_FINANCIAL_RESULTS_API = "https://www.nseindia.com/api/corporates-financial-results"
_ALLOWED_XBRL_HOSTS = {
    "nsearchives.nseindia.com",
    "www.nseindia.com",
    "nseindia.com",
}
_ALLOWED_PERIODS = {"QUARTERLY": "Quarterly", "ANNUAL": "Annual"}


@dataclass(frozen=True)
class NseFinancialResultRecord:
    symbol: str
    period: str
    relating_to: str | None
    financial_year: str | None
    period_start: date | None
    period_end: date | None
    filing_at: datetime | None
    broadcast_at: datetime | None
    consolidation: str | None
    bank_flag: str | None
    xbrl_url: str
    raw_index: int


class NseFinancialResultsFetcher:
    """Discover historical NSE equity financial-result XBRL filings for one symbol."""

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
                "Referer": NSE_FINANCIAL_RESULTS_PAGE,
            },
        )
        try:
            response = await self._client.get(NSE_FINANCIAL_RESULTS_PAGE)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            await self.close()
            raise SourceFetchError("Unable to establish NSE financial-results session") from exc

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def fetch(
        self,
        symbol: str,
        *,
        period: str,
    ) -> list[NseFinancialResultRecord]:
        normalized_symbol = _symbol(symbol)
        normalized_period = normalize_period(period)
        if self._client is None:
            await self.start()
        assert self._client is not None

        response = await self._request(normalized_symbol, normalized_period)
        if response.status_code in {401, 403}:
            await self._refresh_session()
            response = await self._request(normalized_symbol, normalized_period)
        if response.status_code in {401, 403}:
            raise SourceFetchError("NSE financial-results session was rejected")
        if response.status_code == 429:
            raise SourceFetchError("NSE financial-results rate limit exceeded")
        try:
            response.raise_for_status()
            payload = response.json()
            records = parse_nse_financial_results(
                payload,
                expected_symbol=normalized_symbol,
                expected_period=normalized_period,
            )
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise SourceFetchError("Invalid NSE financial-results response") from exc
        return records

    async def fetch_history(self, symbol: str) -> list[NseFinancialResultRecord]:
        records: list[NseFinancialResultRecord] = []
        for period in ("Quarterly", "Annual"):
            records.extend(await self.fetch(symbol, period=period))
        return dedupe_financial_result_records(records)

    async def _request(self, symbol: str, period: str) -> httpx.Response:
        assert self._client is not None
        try:
            return await self._client.get(
                NSE_FINANCIAL_RESULTS_API,
                params={"index": "equities", "symbol": symbol, "period": period},
            )
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch NSE financial-results history") from exc

    async def _refresh_session(self) -> None:
        assert self._client is not None
        try:
            response = await self._client.get(NSE_FINANCIAL_RESULTS_PAGE)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to refresh NSE financial-results session") from exc


def normalize_period(value: str) -> str:
    normalized = value.strip().upper()
    period = _ALLOWED_PERIODS.get(normalized)
    if period is None:
        raise ValueError("period must be Quarterly or Annual")
    return period


def parse_nse_financial_results(
    payload: object,
    *,
    expected_symbol: str,
    expected_period: str,
) -> list[NseFinancialResultRecord]:
    symbol = _symbol(expected_symbol)
    period = normalize_period(expected_period)
    rows = _rows(payload)
    records: list[NseFinancialResultRecord] = []
    for index, row in enumerate(rows):
        row_symbol = _text(_first(row, "symbol", "smSymbol", "companySymbol")).upper()
        if row_symbol and row_symbol != symbol:
            raise ValueError(
                f"NSE financial-results symbol mismatch: expected {symbol}, received {row_symbol}"
            )
        row_period = _text(_first(row, "period", "periodType", "resultPeriod"))
        if row_period:
            normalized_row_period = normalize_period(row_period)
            if normalized_row_period != period:
                continue

        xbrl_value = _first(
            row,
            "xbrl",
            "xbrlFile",
            "xbrlUrl",
            "xbrl_url",
            "ixbrl",
            "ixbrlUrl",
        )
        xbrl_url = normalize_xbrl_url(xbrl_value)
        if xbrl_url is None:
            continue
        records.append(
            NseFinancialResultRecord(
                symbol=symbol,
                period=period,
                relating_to=_optional_text(_first(row, "relatingTo", "relating_to")),
                financial_year=_optional_text(
                    _first(row, "financialYear", "financial_year", "fy")
                ),
                period_start=_date(_first(row, "fromDate", "from_date", "periodStart")),
                period_end=_date(
                    _first(row, "toDate", "to_date", "periodEnd", "relatingTo")
                ),
                filing_at=_datetime(_first(row, "filingDate", "filing_date")),
                broadcast_at=_datetime(
                    _first(row, "broadCastDate", "broadcastDate", "broadcast_date")
                ),
                consolidation=_optional_text(
                    _first(row, "consolidated", "consolidation", "natureOfReport")
                ),
                bank_flag=_optional_text(_first(row, "bank", "bankFlag", "bank_flag")),
                xbrl_url=xbrl_url,
                raw_index=index,
            )
        )
    return dedupe_financial_result_records(records)


def dedupe_financial_result_records(
    records: list[NseFinancialResultRecord],
) -> list[NseFinancialResultRecord]:
    by_url: dict[str, NseFinancialResultRecord] = {}
    for record in records:
        existing = by_url.get(record.xbrl_url)
        if existing is None or _record_sort_key(record) > _record_sort_key(existing):
            by_url[record.xbrl_url] = record
    return sorted(by_url.values(), key=_record_sort_key, reverse=True)


def normalize_xbrl_url(value: object) -> str | None:
    candidate = _text(value)
    if not candidate or candidate.lower() in {"na", "n/a", "-", "--"}:
        return None
    url = urljoin(NSE_FINANCIAL_RESULTS_PAGE, candidate)
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().strip(".")
    if parsed.scheme != "https" or hostname not in _ALLOWED_XBRL_HOSTS:
        raise ValueError("NSE financial-results XBRL URL must use an official NSE HTTPS host")
    if not parsed.path:
        raise ValueError("NSE financial-results XBRL URL is missing a path")
    return url


def _rows(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        raise TypeError("NSE financial-results response must be an object or array")
    for key in ("data", "results", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    raise TypeError("NSE financial-results response did not contain a row list")


def _first(row: dict[str, object], *keys: str) -> object:
    for key in keys:
        if key in row and row[key] not in {None, ""}:
            return row[key]
    lowered = {str(key).lower(): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in {None, ""}:
            return value
    return None


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("symbol cannot be empty")
    return normalized


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    result = _text(value)
    return result or None


def _date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip().upper()
    for pattern in (
        "%d-%b-%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%b-%y",
    ):
        try:
            return datetime.strptime(cleaned, pattern).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except ValueError:
        pass
    upper = cleaned.upper()
    for pattern in (
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%b-%Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(upper, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _record_sort_key(record: NseFinancialResultRecord) -> tuple[date, datetime, int]:
    return (
        record.period_end or date.min,
        record.filing_at or record.broadcast_at or datetime.min.replace(tzinfo=UTC),
        -record.raw_index,
    )
