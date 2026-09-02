from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.http_fetcher import SafeHttpFetcher
from app.connectors.india_official import MacroSeriesSpec
from app.ingestion.india_official import OfficialIndiaIngestionService

OFFICIAL_INDIA_DOMAINS = {
    "nseindia.com",
    "nsearchives.nseindia.com",
    "bseindia.com",
    "rbi.org.in",
    "statistics.rbi.org.in",
    "nsdl.co.in",
    "fpi.nsdl.co.in",
    "pilot.fpi.nsdl.co.in",
}


class OfficialIndiaSourcePipeline:
    """Fetches only allowlisted official sources, then normalizes and persists them."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.fetcher = SafeHttpFetcher(allowed_domains=OFFICIAL_INDIA_DOMAINS)
        self.ingestion = OfficialIndiaIngestionService(engine)

    async def ingest_exchange_url(
        self,
        *,
        exchange: str,
        url: str,
    ) -> dict[str, object]:
        document = await self.fetcher.fetch(url)
        return await self.ingestion.ingest_exchange_payload(
            exchange=exchange,
            data=document.content,
            media_type=document.media_type,
            source_uri=document.final_url,
        )

    async def ingest_financial_xbrl_url(
        self,
        *,
        exchange: str,
        identifier: str,
        url: str,
        title: str,
        published_at: datetime | None = None,
    ) -> dict[str, object]:
        document = await self.fetcher.fetch(url)
        return await self.ingestion.ingest_financial_xbrl(
            exchange=exchange,
            identifier=identifier,
            data=document.content,
            media_type=document.media_type,
            source_uri=document.final_url,
            title=title,
            published_at=published_at,
        )

    async def ingest_rbi_series_url(
        self,
        *,
        url: str,
        spec: MacroSeriesSpec,
        title: str,
    ) -> dict[str, object]:
        document = await self.fetcher.fetch(url)
        return await self.ingestion.ingest_rbi_series(
            data=document.content,
            media_type=document.media_type,
            spec=spec,
            source_uri=document.final_url,
            title=title,
        )

    async def ingest_nsdl_flows_url(
        self,
        *,
        url: str,
        title: str = "NSDL FPI/DII investment data",
    ) -> dict[str, object]:
        document = await self.fetcher.fetch(url)
        return await self.ingestion.ingest_nsdl_flows(
            data=document.content,
            media_type=document.media_type,
            source_uri=document.final_url,
            title=title,
        )
