from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Self

import httpx

from app.connectors.http_fetcher import SourceFetchError
from app.connectors.nse_financial_results import (
    NSE_FINANCIAL_RESULTS_PAGE,
    normalize_xbrl_url,
)

MAX_XBRL_BYTES = 25 * 1024 * 1024
_ACCEPTED_MEDIA_TYPES = {
    "application/xbrl+xml",
    "application/xml",
    "text/xml",
    "text/html",
    "application/xhtml+xml",
}


@dataclass(frozen=True)
class FetchedNseXbrl:
    source_url: str
    media_type: str
    content: bytes
    sha256: str


class NseFinancialXbrlFetcher:
    """Fetch official NSE-hosted XBRL/iXBRL documents with strict payload validation."""

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
            timeout=httpx.Timeout(45.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
                ),
                "Accept": (
                    "application/xbrl+xml,application/xml,text/xml,"
                    "application/xhtml+xml,text/html;q=0.9,*/*;q=0.1"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": NSE_FINANCIAL_RESULTS_PAGE,
            },
        )
        await self._refresh_session()

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def fetch(self, source_url: str) -> FetchedNseXbrl:
        url = normalize_xbrl_url(source_url)
        if url is None:
            raise ValueError("NSE XBRL source URL cannot be empty")
        if self._client is None:
            await self.start()
        assert self._client is not None

        response = await self._request(url)
        if response.status_code in {401, 403}:
            await self._refresh_session()
            response = await self._request(url)
        if response.status_code in {401, 403}:
            raise SourceFetchError("NSE XBRL session was rejected")
        if response.status_code == 429:
            raise SourceFetchError("NSE XBRL rate limit exceeded")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch NSE XBRL document") from exc

        content = response.content
        media_type = validate_xbrl_payload(
            content,
            content_type=response.headers.get("content-type"),
            source_url=url,
        )
        return FetchedNseXbrl(
            source_url=url,
            media_type=media_type,
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    async def _request(self, url: str) -> httpx.Response:
        assert self._client is not None
        try:
            return await self._client.get(url)
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to fetch NSE XBRL document") from exc

    async def _refresh_session(self) -> None:
        assert self._client is not None
        try:
            response = await self._client.get(NSE_FINANCIAL_RESULTS_PAGE)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceFetchError("Unable to establish NSE financial-results document session") from exc


def validate_xbrl_payload(
    content: bytes,
    *,
    content_type: str | None,
    source_url: str,
    max_bytes: int = MAX_XBRL_BYTES,
) -> str:
    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")
    if not content:
        raise ValueError("NSE XBRL document is empty")
    if len(content) > max_bytes:
        raise ValueError(
            f"NSE XBRL document exceeds the {max_bytes}-byte safety limit"
        )
    normalized_url = normalize_xbrl_url(source_url)
    if normalized_url is None:
        raise ValueError("NSE XBRL source URL cannot be empty")

    declared = (content_type or "").split(";", 1)[0].strip().lower()
    sniffed = sniff_xbrl_media_type(content)
    if declared in _ACCEPTED_MEDIA_TYPES:
        if sniffed is None:
            raise ValueError("NSE XBRL payload content does not match its declared media type")
        if declared in {"text/html", "application/xhtml+xml"} and sniffed not in {
            "text/html",
            "application/xhtml+xml",
        }:
            raise ValueError("NSE inline-XBRL payload is not HTML/XHTML")
        if declared in {"application/xbrl+xml", "application/xml", "text/xml"} and sniffed not in {
            "application/xml",
            "application/xbrl+xml",
        }:
            raise ValueError("NSE XBRL payload is not XML")
        return declared

    if declared in {"", "application/octet-stream", "binary/octet-stream", "text/plain"}:
        if sniffed is None:
            raise ValueError("NSE XBRL payload could not be identified as XML or inline XBRL")
        return sniffed

    raise ValueError(f"Unsupported NSE XBRL media type: {declared or 'unknown'}")


def sniff_xbrl_media_type(content: bytes) -> str | None:
    sample = content[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    has_html = b"<html" in sample[:2048]
    has_inline_xbrl = b"xmlns:ix" in sample or b"<ix:" in sample or b" inline" in sample
    if has_html and has_inline_xbrl:
        return "application/xhtml+xml"
    if sample.startswith((b"<!doctype html", b"<html")):
        return "text/html"
    if sample.startswith(b"<?xml") or b"<xbrl" in sample[:1024] or b":xbrl" in sample[:1024]:
        return "application/xml"
    return None
