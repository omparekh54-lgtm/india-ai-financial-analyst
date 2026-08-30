from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import urlencode, urlparse

import httpx

from app.connectors.http_fetcher import FetchedDocument, SourceFetchError

_BSE_HOST = "api.bseindia.com"
_BSE_API_PATH = "/BseIndiaAPI/api/AnnSubCategoryGetData/w"
_MAX_PUBLIC_PAGES = 8


class BsePublicAnnouncementsFetcher:
    """Bounded development adapter for BSE's observed public corporate-announcement JSON route."""

    async def fetch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        lookback_days: int = 1,
        max_pages: int = 4,
    ) -> FetchedDocument:
        _validate_bse_api_url(url)
        lookback_days = max(0, min(lookback_days, 7))
        max_pages = max(1, min(max_pages, _MAX_PUBLIC_PAGES))
        today = date.today()
        start = today - timedelta(days=lookback_days)
        request_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.bseindia.com/",
        }
        if headers:
            request_headers.update(headers)

        rows: list[dict[str, object]] = []
        last_response: httpx.Response | None = None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                headers=request_headers,
            ) as client:
                for page in range(1, max_pages + 1):
                    response = await client.get(
                        _build_page_url(url, page=page, start=start, end=today)
                    )
                    last_response = response
                    if response.status_code in {401, 403}:
                        raise SourceFetchError("BSE public announcements request was rejected")
                    if response.status_code == 429:
                        raise SourceFetchError("BSE public announcements rate limit exceeded")
                    response.raise_for_status()
                    page_rows, total_rows = _parse_bse_page(response.content)
                    rows.extend(page_rows)
                    if not page_rows or len(rows) >= total_rows:
                        break
        except SourceFetchError:
            raise
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch BSE public announcements") from exc

        if last_response is None:
            raise SourceFetchError("BSE public announcements returned no response")
        normalized = json.dumps({"Table": rows}, ensure_ascii=False).encode("utf-8")
        return FetchedDocument(
            final_url=str(last_response.url),
            media_type="application/json",
            content=normalized,
            etag=last_response.headers.get("etag"),
            last_modified=last_response.headers.get("last-modified"),
        )


def _parse_bse_page(data: bytes) -> tuple[list[dict[str, object]], int]:
    payload = json.loads(data.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise TypeError("BSE announcements response was not an object")
    raw_rows = payload.get("Table")
    if not isinstance(raw_rows, list):
        raise TypeError("BSE announcements response did not contain Table rows")
    rows = [row for row in raw_rows if isinstance(row, dict)]

    total = len(rows)
    table1 = payload.get("Table1")
    if isinstance(table1, list) and table1 and isinstance(table1[0], dict):
        candidate = table1[0].get("ROWCNT") or table1[0].get("rowcnt")
        try:
            if candidate is not None:
                total = max(total, int(candidate))
        except (TypeError, ValueError):
            pass
    return rows, total


def _build_page_url(url: str, *, page: int, start: date, end: date) -> str:
    base = f"https://{_BSE_HOST}{_BSE_API_PATH}"
    query = urlencode(
        {
            "pageno": page,
            "strCat": "-1",
            "strPrevDate": start.strftime("%d%m%Y"),
            "strScrip": "",
            "strSearch": "P",
            "strToDate": end.strftime("%d%m%Y"),
            "strType": "C",
            "subcategory": "-1",
        }
    )
    return f"{base}?{query}"


def _validate_bse_api_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != _BSE_HOST:
        raise ValueError("BSE public feed URL must use the official api.bseindia.com HTTPS host")
    if parsed.path != _BSE_API_PATH:
        raise ValueError("Only the observed BSE corporate-announcements API path is allowed")
