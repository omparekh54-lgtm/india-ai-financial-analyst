from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.bse_public import BsePublicAnnouncementsFetcher
from app.connectors.http_fetcher import FetchedDocument, SafeHttpFetcher
from app.connectors.india_official import MacroSeriesSpec
from app.connectors.nse_public import NsePublicAnnouncementsFetcher
from app.ingestion.exchange_documents import ExchangeDocumentIngestor
from app.ingestion.india_official import OfficialIndiaIngestionService
from app.ingestion.official_pipeline import OFFICIAL_INDIA_DOMAINS
from app.repositories.official_feeds import ClaimedFeed, OfficialFeedRepository

_MAX_EXCHANGE_DOCUMENTS_PER_RUN = 12


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
        self.nse_public = NsePublicAnnouncementsFetcher()
        self.bse_public = BsePublicAnnouncementsFetcher()
        self.ingestion = OfficialIndiaIngestionService(engine)
        self.documents = ExchangeDocumentIngestor(engine)

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
            except Exception as exc:  # noqa: BLE001 - isolate one feed from the rest of the batch
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
        document = await self._fetch_claim_source(claim, headers=headers or None)

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

    async def _fetch_claim_source(
        self,
        claim: ClaimedFeed,
        *,
        headers: dict[str, str] | None,
    ) -> FetchedDocument:
        fetch_mode = str(claim.feed.parser_config.get("fetch_mode") or "").strip()
        if claim.feed.provider == "NSE" and fetch_mode == "nse_public_session":
            return await self.nse_public.fetch(claim.feed.source_url, headers=headers)
        if claim.feed.provider == "BSE" and fetch_mode == "bse_public_api":
            return await self.bse_public.fetch(
                claim.feed.source_url,
                headers=headers,
                lookback_days=_bounded_int(
                    claim.feed.parser_config.get("lookback_days"), default=1, minimum=0, maximum=7
                ),
                max_pages=_bounded_int(
                    claim.feed.parser_config.get("max_pages"), default=4, minimum=1, maximum=8
                ),
            )
        return await self.fetcher.fetch(claim.feed.source_url, headers=headers)

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
            result = await self.ingestion.ingest_exchange_payload(
                exchange=feed.exchange,
                data=data,
                media_type=media_type,
                source_uri=final_url,
            )
            candidates = result.pop("document_candidates", [])
            if isinstance(candidates, list):
                result["document_ingestion"] = await self._follow_exchange_documents(candidates)
            return result

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

    async def _follow_exchange_documents(
        self,
        candidates: list[object],
    ) -> dict[str, object]:
        attempted = 0
        parsed = 0
        xbrl_normalized = 0
        failures: list[dict[str, str]] = []

        for raw_candidate in candidates[:_MAX_EXCHANGE_DOCUMENTS_PER_RUN]:
            if not isinstance(raw_candidate, dict):
                continue
            candidate = raw_candidate
            event_id = _uuid(candidate.get("event_id"))
            security_id = _uuid(candidate.get("security_id"))
            exchange = _optional_string(candidate.get("exchange"))
            identifier = _optional_string(candidate.get("identifier"))
            headline = _optional_string(candidate.get("headline")) or "Exchange disclosure"
            published_at = _configured_datetime(candidate.get("published_at"))
            if not event_id or not security_id or not exchange or not identifier:
                continue

            urls: list[tuple[str, str]] = []
            attachment_url = _optional_string(candidate.get("attachment_url"))
            xbrl_url = _optional_string(candidate.get("xbrl_url"))
            if attachment_url:
                urls.append((attachment_url, _document_role(candidate.get("event_type"))))
            if xbrl_url and xbrl_url != attachment_url:
                urls.append((xbrl_url, "xbrl"))

            for url, role in urls:
                attempted += 1
                try:
                    fetched = await self.fetcher.fetch(url)
                    document_result = await self.documents.ingest(
                        event_id=event_id,
                        security_id=security_id,
                        source_uri=fetched.final_url,
                        media_type=fetched.media_type,
                        content=fetched.content,
                        title=headline,
                        published_at=published_at,
                        document_role=role,
                        metadata={
                            "exchange": exchange,
                            "identifier": identifier,
                            "event_type": candidate.get("event_type"),
                        },
                    )
                    if document_result.get("parse_status") == "parsed":
                        parsed += 1

                    if role == "xbrl" or fetched.media_type in {
                        "application/xbrl+xml",
                        "application/xml",
                        "text/xml",
                    }:
                        financial_result = await self.ingestion.ingest_financial_xbrl(
                            exchange=exchange,
                            identifier=identifier,
                            data=fetched.content,
                            media_type=fetched.media_type,
                            source_uri=fetched.final_url,
                            title=headline,
                            published_at=published_at,
                        )
                        source_id = _uuid(financial_result.get("source_id"))
                        if source_id:
                            await self.documents.link_existing_source(
                                event_id=event_id,
                                source_id=source_id,
                                document_role="xbrl",
                                media_type=fetched.media_type,
                                metadata={
                                    "normalized_count": financial_result.get("normalized_count", 0),
                                    "derived_count": financial_result.get("derived_count", 0),
                                },
                            )
                        xbrl_normalized += int(financial_result.get("normalized_count") or 0)
                except Exception as exc:  # noqa: BLE001 - one bad filing must not fail exchange polling
                    failures.append(
                        {
                            "url": url,
                            "event_id": str(event_id),
                            "error_type": type(exc).__name__,
                        }
                    )

        return {
            "candidate_count": min(len(candidates), _MAX_EXCHANGE_DOCUMENTS_PER_RUN),
            "attempted_count": attempted,
            "parsed_document_count": parsed,
            "xbrl_normalized_fact_count": xbrl_normalized,
            "failure_count": len(failures),
            "failures": failures[:20],
        }


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


def _uuid(value: object) -> UUID | None:
    candidate = _optional_string(value)
    if not candidate:
        return None
    try:
        return UUID(candidate)
    except ValueError:
        return None


def _document_role(event_type: object) -> str:
    value = str(event_type or "")
    return {
        "earnings_transcript": "transcript",
        "earnings_call": "transcript",
        "investor_presentation": "presentation",
        "annual_report": "annual_report",
    }.get(value, "attachment")


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        candidate = int(value) if value is not None else default
    except (TypeError, ValueError):
        candidate = default
    return max(minimum, min(candidate, maximum))
