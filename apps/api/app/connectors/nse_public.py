from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx

from app.connectors.http_fetcher import FetchedDocument, SourceFetchError
from app.connectors.india_official import NSE_ANNOUNCEMENTS_PAGE

_NSE_HOST = "www.nseindia.com"
_NSE_ANNOUNCEMENTS_API_PATH = "/api/corporate-announcements"


class NsePublicAnnouncementsFetcher:
    """Development/public-web adapter for NSE announcements that require a browser session cookie."""

    async def fetch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> FetchedDocument:
        _validate_nse_api_url(url)
        request_headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": NSE_ANNOUNCEMENTS_PAGE,
        }
        if headers:
            request_headers.update(headers)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                headers=request_headers,
            ) as client:
                landing = await client.get(NSE_ANNOUNCEMENTS_PAGE)
                landing.raise_for_status()
                response = await client.get(url)
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch NSE public announcements") from exc

        if response.status_code == 304:
            return FetchedDocument(
                final_url=str(response.url),
                media_type="application/json",
                content=b"",
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
                not_modified=True,
            )
        if response.status_code in {401, 403}:
            raise SourceFetchError("NSE public announcements session was rejected")
        if response.status_code == 429:
            raise SourceFetchError("NSE public announcements rate limit exceeded")
        try:
            response.raise_for_status()
            normalized = normalize_nse_announcement_json(response.content)
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceFetchError("Invalid NSE public announcements response") from exc

        return FetchedDocument(
            final_url=str(response.url),
            media_type="application/json",
            content=normalized,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )


def normalize_nse_announcement_json(data: bytes) -> bytes:
    """Translate current NSE API fields into the generic exchange-disclosure parser vocabulary."""
    payload = json.loads(data.decode("utf-8-sig"))
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("NSE announcements response did not contain a row list")

    normalized: list[dict[str, object]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        normalized.append(
            {
                "symbol": raw.get("symbol"),
                "company name": raw.get("sm_name") or raw.get("companyName"),
                "subject": raw.get("desc") or raw.get("subject"),
                "details": raw.get("attchmntText") or raw.get("details"),
                "broadcast date/time": raw.get("an_dt") or raw.get("sort_date") or raw.get("dt"),
                "attachment": raw.get("attchmntFile") or raw.get("attachment"),
                "xbrl": raw.get("xbrl") or raw.get("xbrlFile"),
                "isin": raw.get("sm_isin"),
                "sequence id": raw.get("seq_id"),
                "industry": raw.get("smIndustry"),
                "exchange dissemination time": raw.get("exchdisstime"),
            }
        )
    return json.dumps(normalized, ensure_ascii=False).encode("utf-8")


def _validate_nse_api_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != _NSE_HOST:
        raise ValueError("NSE public feed URL must use the official www.nseindia.com HTTPS host")
    if parsed.path != _NSE_ANNOUNCEMENTS_API_PATH:
        raise ValueError("Only the official NSE corporate-announcements API path is allowed")
