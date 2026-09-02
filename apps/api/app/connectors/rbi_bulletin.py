from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime
from typing import Self
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.connectors.http_fetcher import SourceFetchError
from app.ingestion.macro import MacroObservation

RBI_BULLETIN_PAGE = "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx"
_TEN_YEAR_LABEL = "10-Year G-Sec Par Yield (FBIL)"
_ALLOWED_HOSTS = {"www.rbi.org.in", "rbi.org.in"}
_ALLOWED_DETAIL_PATH = "/scripts/bs_viewbulletin.aspx"
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


class RbiBulletinTenYearYieldFetcher:
    """Fetch the latest RBI Bulletin Select Economic Indicators 10-year G-Sec par yield."""

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
                "Accept": "text/html,application/xhtml+xml,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def fetch(self) -> MacroObservation:
        if self._client is None:
            await self.start()
        assert self._client is not None

        try:
            landing = await self._client.get(RBI_BULLETIN_PAGE)
            landing.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch the RBI Bulletin landing page") from exc

        detail_url = parse_latest_select_economic_indicators_url(landing.text)
        try:
            detail = await self._client.get(detail_url, headers={"Referer": RBI_BULLETIN_PAGE})
            detail.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch RBI Select Economic Indicators") from exc

        return parse_rbi_bulletin_ten_year_yield(detail.text, source_uri=detail_url)


def parse_latest_select_economic_indicators_url(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for anchor in soup.find_all("a", href=True):
        text_value = _clean_text(anchor.get_text(" ", strip=True)).lower()
        if "select economic indicators" not in text_value:
            continue
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(RBI_BULLETIN_PAGE, href)
        _validate_detail_url(url)
        candidates.append(url)
    if not candidates:
        raise ValueError("RBI Bulletin landing page did not expose Select Economic Indicators")

    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1:
        raise ValueError(
            "RBI Bulletin landing page exposed multiple Select Economic Indicators detail URLs"
        )
    return unique[0]


def parse_rbi_bulletin_ten_year_yield(
    html: str,
    *,
    source_uri: str,
) -> MacroObservation:
    _validate_detail_url(source_uri)
    soup = BeautifulSoup(html, "html.parser")
    publication_date = _publication_date(soup)
    row = _find_series_row(soup)
    cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
    if len(cells) < 2 or _TEN_YEAR_LABEL.lower() not in cells[0].lower():
        raise ValueError("RBI Bulletin 10-year yield row has an invalid structure")

    values = [_number(value) for value in cells[1:]]
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        raise ValueError("RBI Bulletin 10-year yield row contains no numeric observations")
    latest_value = numeric_values[-1]

    observation_date = _latest_month_end_before_row(row, publication_date=publication_date)
    if observation_date > publication_date:
        raise ValueError("RBI Bulletin observation date cannot be after publication date")

    return MacroObservation(
        series_key="india_10y_yield",
        observation_date=observation_date,
        value=latest_value,
        unit="percent",
        released_at=datetime.combine(publication_date, datetime.min.time(), tzinfo=UTC),
        metadata={
            "source": "RBI",
            "source_page": RBI_BULLETIN_PAGE,
            "source_uri": source_uri,
            "publication_date": publication_date.isoformat(),
            "series_label": _TEN_YEAR_LABEL,
            "observation_basis": "latest_month_column",
            "provenance_class": "official_source",
        },
    )


def _find_series_row(soup: BeautifulSoup) -> Tag:
    for row in soup.find_all("tr"):
        if _TEN_YEAR_LABEL.lower() in _clean_text(row.get_text(" ", strip=True)).lower():
            return row
    raise ValueError("RBI Bulletin does not contain the 10-Year G-Sec Par Yield (FBIL) row")


def _publication_date(soup: BeautifulSoup) -> date:
    text_value = _clean_text(soup.get_text(" ", strip=True))
    match = re.search(
        r"\bDate\s*:\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
        r"\s+(\d{1,2}),\s+(\d{4})\b",
        text_value,
        flags=re.IGNORECASE,
    )
    if match is None:
        raise ValueError("RBI Bulletin detail page is missing its publication date")
    month = _MONTHS[match.group(1).lower()]
    return date(int(match.group(3)), month, int(match.group(2)))


def _latest_month_end_before_row(row: Tag, *, publication_date: date) -> date:
    previous_rows = list(row.find_all_previous("tr", limit=12))
    month_number: int | None = None
    year: int | None = None

    for previous in previous_rows:
        tokens = _tokens(previous.get_text(" ", strip=True))
        month_candidates = [_month_number(token) for token in tokens]
        clean_months = [value for value in month_candidates if value is not None]
        if month_number is None and clean_months:
            month_number = clean_months[-1]
        year_candidates = [
            int(token)
            for token in tokens
            if re.fullmatch(r"20\d{2}", token) is not None
        ]
        if year is None and year_candidates:
            year = year_candidates[-1]
        if month_number is not None and year is not None:
            break

    if month_number is None:
        raise ValueError("RBI Bulletin could not resolve the latest month for the 10-year yield")
    if year is None:
        year = publication_date.year
        if month_number > publication_date.month:
            year -= 1

    last_day = calendar.monthrange(year, month_number)[1]
    return date(year, month_number, last_day)


def _tokens(value: str) -> list[str]:
    return [token.strip(".,:;()[]") for token in _clean_text(value).split()]


def _month_number(token: str) -> int | None:
    return _MONTHS.get(token.lower())


def _number(value: str) -> float | None:
    cleaned = value.replace(",", "").strip()
    if not cleaned or cleaned in {"-", "--", ".."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _validate_detail_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_HOSTS:
        raise ValueError("RBI Bulletin detail URL must use the official RBI HTTPS host")
    if parsed.path.lower() != _ALLOWED_DETAIL_PATH or not parsed.query:
        raise ValueError("RBI Bulletin detail URL must use BS_ViewBulletin.aspx with an Id")
    if re.fullmatch(r"Id=\d+", parsed.query, flags=re.IGNORECASE) is None:
        raise ValueError("RBI Bulletin detail URL must contain one numeric Id parameter")
