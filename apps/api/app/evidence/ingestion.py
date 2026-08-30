from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.agents.contracts import EvidenceRef
from app.connectors.http_fetcher import SafeHttpFetcher
from app.connectors.tavily import TavilyConnector
from app.documents.parser import DocumentParseError, chunk_document, parse_document


PRIMARY_SOURCE_DOMAINS = {
    "nseindia.com",
    "nsearchives.nseindia.com",
    "bseindia.com",
    "sebi.gov.in",
    "rbi.org.in",
    "rbidocs.rbi.org.in",
    "nsdl.co.in",
}


@dataclass
class EvidenceIngestionResult:
    evidence: list[EvidenceRef] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


class OfficialEvidenceIngestor:
    """Discovers official Indian sources, fetches them safely, and creates traceable chunks."""

    def __init__(self, search: TavilyConnector) -> None:
        self.search = search
        self.fetcher = SafeHttpFetcher(allowed_domains=PRIMARY_SOURCE_DOMAINS)

    async def discover_and_ingest(
        self,
        query: str,
        *,
        max_documents: int = 3,
    ) -> EvidenceIngestionResult:
        max_documents = max(1, min(max_documents, 5))
        results = await self.search.search(
            query,
            max_results=max_documents,
            include_domains=sorted(PRIMARY_SOURCE_DOMAINS),
        )
        output = EvidenceIngestionResult()

        for result in results[:max_documents]:
            try:
                fetched = await self.fetcher.fetch(result.source_uri)
                parsed = parse_document(
                    fetched.content,
                    fetched.media_type,
                    title=result.title,
                )
                source_type, source_priority = _classify_source(fetched.final_url)
                for chunk in chunk_document(parsed, max_chars=3500, overlap_chars=300):
                    output.evidence.append(
                        EvidenceRef(
                            source_type=source_type,
                            source_uri=fetched.final_url,
                            title=parsed.title,
                            retrieved_at=datetime.now(UTC).isoformat(),
                            freshness="near_live",
                            excerpt=chunk.content,
                            page_number=chunk.page_number,
                            checksum=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                            source_priority=source_priority,
                        )
                    )
            except (DocumentParseError, RuntimeError, ValueError) as exc:
                output.failures.append(f"{result.source_uri}: {type(exc).__name__}")

        return output


def _classify_source(url: str) -> tuple[str, int]:
    host = (urlparse(url).hostname or "").lower()
    if host == "nseindia.com" or host.endswith(".nseindia.com"):
        return "exchange_filing", 1
    if host == "bseindia.com" or host.endswith(".bseindia.com"):
        return "exchange_filing", 1
    if host == "sebi.gov.in" or host.endswith(".sebi.gov.in"):
        return "regulator", 1
    if host == "rbi.org.in" or host.endswith(".rbi.org.in"):
        return "official_macro", 1
    if host == "nsdl.co.in" or host.endswith(".nsdl.co.in"):
        return "official_flow", 1
    return "official_web", 2
