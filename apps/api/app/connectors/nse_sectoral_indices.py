from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass

import httpx

from app.connectors.http_fetcher import SourceFetchError

# NSE's interactive per-symbol quote API (api/quote-equity, used by nse_classification.py) is
# reliably blocked from GitHub Actions and other non-Indian datacenter IPs -- confirmed by
# repeated timeouts even with retries. Even when reachable, that API's four-level taxonomy
# (macro sector / sector / industry / basic industry) is only populated by NSE for securities
# large/liquid enough to sit in a major index -- roughly 750 of the ~2,300 NSE EQ universe (see
# the "universe_ceiling_note" already documented in backfill_nse_industry_classification.py).
#
# This connector instead pulls NSE's own bulk constituent file for the NIFTY Total Market Index
# from nsearchives.nseindia.com -- a static-file archive subdomain, not the interactive site --
# which is reachable from GitHub Actions and covers that same ~750-security ceiling with a
# single sector-level label per company (NSE's own "Industry" column, which in practice matches
# the broad "sector" tier of NSE's four-level taxonomy, e.g. "Financial Services", "Healthcare").
# It does not provide the finer industry / macro-sector / basic-industry tiers, so this is a
# genuinely lower-resolution, still-official source -- not a drop-in replacement for the 4-tier
# connector, which remains available for whenever NSE's quote API is reachable again (e.g. a
# self-hosted runner in India) or a licensed provider is added.
NSE_TOTAL_MARKET_INDEX_CSV = (
    "https://nsearchives.nseindia.com/content/indices/ind_niftytotalmarket_list.csv"
)
NSE_NIFTY50_INDEX_CSV = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
)


@dataclass(frozen=True)
class NseSectoralIndexEntry:
    isin: str
    symbol: str
    company_name: str
    sector: str


@dataclass(frozen=True)
class NseSectoralIndexResult:
    source_url: str
    response_sha256: str
    entries: tuple[NseSectoralIndexEntry, ...]


class NseSectoralIndexFetcher:
    """Fetch an official NSE index constituent list (including its reported industry label)."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        source_url: str = NSE_TOTAL_MARKET_INDEX_CSV,
        index_name: str = "NIFTY Total Market",
    ) -> None:
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("timeout_seconds must be between 0 and 120")
        if not source_url.startswith("https://nsearchives.nseindia.com/content/indices/"):
            raise ValueError("source_url must be an official NSE indices archive URL")
        if not index_name.strip():
            raise ValueError("index_name is required")
        self.timeout_seconds = timeout_seconds
        self.source_url = source_url
        self.index_name = index_name.strip()

    async def fetch(self) -> NseSectoralIndexResult:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
            ),
            "Accept": "text/csv,application/octet-stream,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds, connect=10.0),
                follow_redirects=True,
                headers=headers,
            ) as client:
                last_exc: httpx.HTTPError | None = None
                for attempt in range(3):
                    try:
                        response = await client.get(self.source_url)
                        response.raise_for_status()
                        break
                    except httpx.HTTPError as exc:
                        last_exc = exc
                        response = None
                        if attempt < 2:
                            continue
                if response is None:
                    assert last_exc is not None
                    raise last_exc
        except httpx.HTTPError as exc:
            raise SourceFetchError(
                f"Unable to fetch {self.index_name} constituent list"
            ) from exc

        checksum = hashlib.sha256(response.content).hexdigest()
        entries = parse_sectoral_index_csv(response.text)
        if not entries:
            raise SourceFetchError(f"{self.index_name} constituent list contained no rows")
        return NseSectoralIndexResult(
            source_url=self.source_url,
            response_sha256=checksum,
            entries=tuple(entries),
        )


def parse_sectoral_index_csv(text: str) -> list[NseSectoralIndexEntry]:
    reader = csv.DictReader(io.StringIO(text))
    required = {"Company Name", "Industry", "Symbol", "ISIN Code"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise SourceFetchError(
            "NSE Total Market Index CSV is missing expected columns "
            f"(found: {reader.fieldnames})"
        )
    entries: list[NseSectoralIndexEntry] = []
    seen_isins: set[str] = set()
    for row in reader:
        isin = (row.get("ISIN Code") or "").strip().upper()
        symbol = (row.get("Symbol") or "").strip().upper()
        sector = (row.get("Industry") or "").strip()
        company_name = (row.get("Company Name") or "").strip()
        if not isin or not symbol or not sector:
            continue
        if isin in seen_isins:
            continue
        seen_isins.add(isin)
        entries.append(
            NseSectoralIndexEntry(
                isin=isin, symbol=symbol, company_name=company_name, sector=sector
            )
        )
    return entries
