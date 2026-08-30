from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx


class UnsafeSourceUrl(ValueError):
    pass


class SourceFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchedDocument:
    final_url: str
    media_type: str
    content: bytes
    etag: str | None = None
    last_modified: str | None = None


class SafeHttpFetcher:
    """Fetches allowlisted public evidence while blocking SSRF and oversized downloads."""

    def __init__(
        self,
        *,
        allowed_domains: set[str],
        max_bytes: int = 25 * 1024 * 1024,
        max_redirects: int = 3,
    ) -> None:
        self.allowed_domains = {domain.lower().strip(".") for domain in allowed_domains}
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects

    async def fetch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> FetchedDocument:
        current_url = url
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            headers={
                "User-Agent": "IndiaAIFinancialAnalyst/0.3 evidence-fetcher",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            for _ in range(self.max_redirects + 1):
                await self._validate_url(current_url)
                async with client.stream("GET", current_url, headers=headers) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceFetchError("Redirect response did not include a location")
                        current_url = urljoin(current_url, location)
                        continue

                    response.raise_for_status()
                    media_type = response.headers.get("content-type", "application/octet-stream")
                    media_type = media_type.split(";", 1)[0].strip().lower()
                    if not _supported_media_type(media_type, current_url):
                        raise SourceFetchError(f"Unsupported evidence content type: {media_type}")

                    declared_length = response.headers.get("content-length")
                    if declared_length and int(declared_length) > self.max_bytes:
                        raise SourceFetchError("Evidence document exceeds the configured size limit")

                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self.max_bytes:
                            raise SourceFetchError("Evidence document exceeds the configured size limit")

                    return FetchedDocument(
                        final_url=current_url,
                        media_type=media_type,
                        content=bytes(content),
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                    )

        raise SourceFetchError("Too many redirects while fetching evidence")

    async def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise UnsafeSourceUrl("Only HTTPS evidence URLs are allowed")
        if parsed.username or parsed.password:
            raise UnsafeSourceUrl("Credentials in evidence URLs are not allowed")
        hostname = (parsed.hostname or "").lower().strip(".")
        if not hostname or not self._domain_allowed(hostname):
            raise UnsafeSourceUrl("Evidence URL host is not allowlisted")

        addresses = await asyncio.to_thread(_resolve_addresses, hostname, parsed.port or 443)
        if not addresses:
            raise UnsafeSourceUrl("Evidence host could not be resolved")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise UnsafeSourceUrl("Evidence URL resolved to a non-public address")

    def _domain_allowed(self, hostname: str) -> bool:
        return any(hostname == domain or hostname.endswith(f".{domain}") for domain in self.allowed_domains)


def _supported_media_type(media_type: str, url: str) -> bool:
    supported = {
        "application/pdf",
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "application/json",
        "text/json",
        "application/xml",
        "text/xml",
    }
    if media_type in supported:
        return True
    if media_type != "application/octet-stream":
        return False
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    return suffix in {".csv", ".json", ".xml", ".pdf", ".html", ".txt"}


def _resolve_addresses(hostname: str, port: int) -> set[str]:
    return {
        item[4][0]
        for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    }
