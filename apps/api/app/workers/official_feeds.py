from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.http_fetcher import SafeHttpFetcher
from app.connectors.india_official import MacroSeriesSpec
from app.ingestion.india_official import OfficialIndiaIngestionService
from app.ingestion.official_pipeline import OFFICIAL_INDIA_DOMAINS
from app.repositories.official_feeds import ClaimedFeed, OfficialFeedRepository


class OfficialFeedWorker:
    """Runs due official-source feeds with leases, checkpoints and an external-data kill switch."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        external_data_enabled: bool,
    ) -> None:
        self.external_data_enabled = external_data_enabled
        self.repository = OfficialFeedRepository(engine)
        self.fetcher = SafeHttpFetcher(allowed_domains=OFFICIAL_INDIA_DOMAINS)
        self.ingestion = OfficialIndiaIngestionService(engine)

    async def run_once(self, *, limit: int = 4) -> dict[str, object]:
        if not self.external_data_enabled:
            return {
                "status": "skipped",
                "reason": "external_data_calls_disabled",
                "claimed_count": 0,
            }

        claims = await self.repository.claim_due(limit=limit)
        results: list[dict[str, object]] = []
        success_count = 0
        failed_count = 0
        not_modified_count = 0

        for claim in claims:
            try:
                outcome = await self._run_claim(claim)
                results.append(outcome)
                if outcome["status"] == "not_modified":
                    not_modified_count += 1
                else:
                    success_count += 1
            except Exception as exc:
                await self.repository.fail(claim, exc)
                failed_count += 1
                results.append(
                    {
                        "feed_id": str(claim.feed.id),
                        "name": claim.feed.name,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    }
                )

        return {
            "status": "completed",
            "claimed_count": len(claims),
            "success_count": success_count,
            "not_modified_count": not_modified_count,
            "failed_count": failed_count,
            "results": results,
        }

    async def _run_claim(self, claim: ClaimedFeed) -> dict[str, object]:
        headers = _conditional_headers(claim)
        document = await self.fetcher.fetch(claim.feed.source_url, headers=headers or None)

        if document.not_modified:
            await self.repository.complete(
                claim,
                status="not_modified",
                result={"not_modified": True},
                etag=document.etag or claim.feed.etag,
                last_modified=document.last_modified or claim.feed.last_modified,
            )
            return {
                "feed_id": str(claim.feed.id),
                "name": claim.feed.name,
                "status": "not_modified",
            }

        result = await self._dispatch(claim, document.content, document.media_type, document.final_url)
        await self.repository.complete(
            claim,
            status="success",
            result=result,
            etag=document.etag,
            last_modified=document.last_modified,
        )
        return {
            "feed_id": str(claim.feed.id),
            "name": claim.feed.name,
            "status": "success",
            "result": result,
        }

    async def _dispatch(
        self,
        claim: ClaimedFeed,
        data: bytes,
        media_type: str,
        final_url: str,
    ) -> dict[str, object]:
        feed = claim.feed
        if feed.feed_type == "exchange_disclosures":
            if not feed.exchange:
                raise ValueError("exchange_disclosures feed requires exchange")
            return await self.ingestion.ingest_exchange_payload(
                exchange=feed.exchange,
                data=data,
                media_type=media_type,
                source_uri=final_url,
            )

        if feed.feed_type == "financial_xbrl":
            if not feed.exchange or not feed.identifier:
                raise ValueError("financial_xbrl feed requires exchange and identifier")
            return await self.ingestion.ingest_financial_xbrl(
                exchange=feed.exchange,
                identifier=feed.identifier,
                data=data,
                media_type=media_type,
                source_uri=final_url,
                title=feed.title or f"{feed.identifier} financial results XBRL",
                published_at=_configured_datetime(feed.parser_config.get("published_at")),
            )

        if feed.feed_type == "rbi_macro":
            series_key = str(feed.parser_config.get("series_key") or "").strip()
            if not series_key:
                raise ValueError("rbi_macro feed requires parser_config.series_key")
            spec = MacroSeriesSpec(
                series_key=series_key,
                unit=_optional_string(feed.parser_config.get("unit")),
                date_column=_optional_string(feed.parser_config.get("date_column")),
                value_column=_optional_string(feed.parser_config.get("value_column")),
            )
            return await self.ingestion.ingest_rbi_series(
                data=data,
                media_type=media_type,
                spec=spec,
                source_uri=final_url,
                title=feed.title or f"RBI {series_key}",
            )

        if feed.feed_type == "nsdl_flows":
            return await self.ingestion.ingest_nsdl_flows(
                data=data,
                media_type=media_type,
                source_uri=final_url,
                title=feed.title or "NSDL FPI/DII investment data",
            )

        raise ValueError(f"Unsupported official feed type: {feed.feed_type}")


def _conditional_headers(claim: ClaimedFeed) -> dict[str, str]:
    headers: dict[str, str] = {}
    if claim.feed.etag:
        headers["If-None-Match"] = claim.feed.etag
    if claim.feed.last_modified:
        headers["If-Modified-Since"] = claim.feed.last_modified
    return headers


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    return candidate or None


def _configured_datetime(value: object) -> datetime | None:
    candidate = _optional_string(value)
    if not candidate:
        return None
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        raise ValueError("configured published_at must include a timezone offset")
    return parsed.astimezone(UTC)
