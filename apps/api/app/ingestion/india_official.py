from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.india_official import (
    MacroSeriesSpec,
    parse_exchange_disclosures,
    parse_nsdl_flows,
    parse_rbi_macro_series,
)
from app.ingestion.exchange import (
    ExchangeDisclosure,
    ExchangeDisclosureIngestor,
    should_follow_exchange_document,
)
from app.ingestion.financials import FinancialFactIngestor
from app.ingestion.macro import MacroObservationIngestor
from app.ingestion.xbrl_financials import parse_financial_xbrl
from app.securities.repository import SecurityMasterRepository


class OfficialIndiaIngestionService:
    """Normalizes official NSE/BSE/RBI/NSDL payloads into provenance-aware storage."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.securities = SecurityMasterRepository(engine)
        self.exchange_ingestor = ExchangeDisclosureIngestor(engine)
        self.financial_ingestor = FinancialFactIngestor(engine)
        self.macro_ingestor = MacroObservationIngestor(engine)

    async def ingest_exchange_payload(
        self,
        *,
        exchange: str,
        data: bytes,
        media_type: str,
        source_uri: str | None = None,
    ) -> dict[str, object]:
        records = parse_exchange_disclosures(
            exchange,
            data,
            media_type,
            source_uri=source_uri,
        )
        security_records = await self.securities.list_all()
        nse_map = {
            str(record.nse_symbol).strip().upper(): record.id
            for record in security_records
            if record.nse_symbol
        }
        bse_map = {
            str(record.bse_code).strip(): record.id
            for record in security_records
            if record.bse_code
        }

        ingested = 0
        unmatched: list[str] = []
        event_types: dict[str, int] = {}
        document_candidates: list[dict[str, object]] = []
        for record in records:
            identifier = record.nse_symbol if record.exchange == "NSE" else record.bse_code
            security_id = (
                nse_map.get(str(identifier).upper())
                if record.exchange == "NSE" and identifier
                else bse_map.get(str(identifier)) if identifier else None
            )
            if security_id is None:
                if identifier and len(unmatched) < 50:
                    unmatched.append(str(identifier))
                continue

            result = await self.exchange_ingestor.ingest(
                ExchangeDisclosure(
                    security_id=security_id,
                    exchange=record.exchange,
                    source_uri=record.source_uri,
                    headline=record.headline,
                    published_at=record.published_at,
                    title=f"{record.company_name}: {record.headline}",
                    excerpt=record.details or record.headline,
                    metadata={
                        "company_name": record.company_name,
                        "nse_symbol": record.nse_symbol,
                        "bse_code": record.bse_code,
                        "attachment_url": record.attachment_url,
                        "xbrl_url": record.xbrl_url,
                        **record.metadata,
                    },
                )
            )
            ingested += 1
            event_types[result.event_type] = event_types.get(result.event_type, 0) + 1

            if should_follow_exchange_document(result.event_type) and (
                record.attachment_url or record.xbrl_url
            ):
                document_candidates.append(
                    {
                        "event_id": str(result.event_id),
                        "source_id": str(result.source_id),
                        "security_id": str(security_id),
                        "exchange": record.exchange,
                        "identifier": identifier,
                        "company_name": record.company_name,
                        "headline": record.headline,
                        "event_type": result.event_type,
                        "published_at": record.published_at.isoformat()
                        if record.published_at
                        else None,
                        "attachment_url": record.attachment_url,
                        "xbrl_url": record.xbrl_url,
                    }
                )

        return {
            "exchange": exchange.strip().upper(),
            "parsed_count": len(records),
            "ingested_count": ingested,
            "unmatched_count": len(records) - ingested,
            "unmatched_identifiers": sorted(set(unmatched)),
            "event_types": event_types,
            "document_candidates": document_candidates,
        }

    async def ingest_financial_xbrl(
        self,
        *,
        exchange: str,
        identifier: str,
        data: bytes,
        media_type: str,
        source_uri: str,
        title: str,
        published_at: datetime | None = None,
    ) -> dict[str, object]:
        normalized_exchange = exchange.strip().upper()
        if normalized_exchange not in {"NSE", "BSE"}:
            raise ValueError("exchange must be NSE or BSE")
        security_id = await self._resolve_security_id(normalized_exchange, identifier)
        if security_id is None:
            raise ValueError(f"Security not found for {normalized_exchange} identifier {identifier!r}")

        raw_facts = parse_financial_xbrl(data, media_type)
        if not raw_facts:
            return {
                "exchange": normalized_exchange,
                "identifier": identifier,
                "security_id": str(security_id),
                "input_count": 0,
                "normalized_count": 0,
                "derived_count": 0,
            }

        source_id = await self._ensure_security_source(
            security_id=security_id,
            source_type="exchange_filing",
            source_uri=source_uri,
            title=title,
            published_at=published_at,
            metadata={
                "exchange": normalized_exchange,
                "identifier": identifier,
                "document_type": "financial_results_xbrl",
                "media_type": media_type,
            },
        )
        result = await self.financial_ingestor.ingest_batch(
            security_id=security_id,
            source_id=source_id,
            facts=raw_facts,
        )
        return {
            "exchange": normalized_exchange,
            "identifier": identifier,
            "security_id": str(security_id),
            "source_id": str(source_id),
            **result,
        }

    async def ingest_rbi_series(
        self,
        *,
        data: bytes,
        media_type: str,
        spec: MacroSeriesSpec,
        source_uri: str,
        title: str,
    ) -> dict[str, object]:
        observations = parse_rbi_macro_series(data, media_type, spec)
        source_id = await self._ensure_global_source(
            source_type="official_macro",
            source_uri=source_uri,
            title=title,
            metadata={"provider": "RBI", "series_key": spec.series_key},
        )
        result = await self.macro_ingestor.ingest_batch(observations, source_id=source_id)
        return {
            "provider": "RBI",
            "series_key": spec.series_key,
            "source_id": str(source_id),
            **result,
        }

    async def ingest_nsdl_flows(
        self,
        *,
        data: bytes,
        media_type: str,
        source_uri: str,
        title: str = "NSDL FPI/DII investment data",
    ) -> dict[str, object]:
        observations = parse_nsdl_flows(data, media_type)
        source_id = await self._ensure_global_source(
            source_type="official_flow",
            source_uri=source_uri,
            title=title,
            metadata={"provider": "NSDL"},
        )
        result = await self.macro_ingestor.ingest_batch(observations, source_id=source_id)
        return {
            "provider": "NSDL",
            "source_id": str(source_id),
            "series_keys": sorted({item.series_key for item in observations}),
            **result,
        }

    async def _resolve_security_id(self, exchange: str, identifier: str) -> UUID | None:
        candidate = identifier.strip()
        if not candidate:
            return None
        records = await self.securities.list_all()
        if exchange == "NSE":
            upper = candidate.upper()
            return next(
                (
                    record.id
                    for record in records
                    if record.nse_symbol and record.nse_symbol.strip().upper() == upper
                ),
                None,
            )
        digits = "".join(character for character in candidate if character.isdigit())
        return next(
            (
                record.id
                for record in records
                if record.bse_code and record.bse_code.strip() == digits
            ),
            None,
        )

    async def _ensure_security_source(
        self,
        *,
        security_id: UUID,
        source_type: str,
        source_uri: str,
        title: str,
        published_at: datetime | None,
        metadata: dict[str, object],
    ) -> UUID:
        if not source_uri.startswith("https://"):
            raise ValueError("Official source_uri must use HTTPS")
        parameters = {
            "security_id": security_id,
            "source_type": source_type,
            "source_uri": source_uri,
            "title": title,
            "published_at": published_at,
            "retrieved_at": datetime.now(UTC),
            "metadata": json.dumps(metadata),
        }
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    insert into sources (
                        security_id, source_type, source_uri, title, published_at,
                        retrieved_at, freshness, metadata
                    ) values (
                        :security_id, :source_type, :source_uri, :title, :published_at,
                        :retrieved_at, 'periodic', cast(:metadata as jsonb)
                    )
                    on conflict do nothing
                    returning id
                    """
                ),
                parameters,
            )
            source_id = result.scalar_one_or_none()
            if source_id is not None:
                return source_id
            source_id = await connection.scalar(
                text(
                    """
                    select id
                    from sources
                    where security_id = :security_id
                      and source_uri = :source_uri
                      and published_at is not distinct from :published_at
                    order by retrieved_at desc
                    limit 1
                    """
                ),
                {
                    "security_id": security_id,
                    "source_uri": source_uri,
                    "published_at": published_at,
                },
            )
            if source_id is None:
                raise RuntimeError("Unable to resolve official security source")
            return source_id

    async def _ensure_global_source(
        self,
        *,
        source_type: str,
        source_uri: str,
        title: str,
        metadata: dict[str, object],
    ) -> UUID:
        if not source_uri.startswith("https://"):
            raise ValueError("Official source_uri must use HTTPS")

        parameters = {
            "source_type": source_type,
            "source_uri": source_uri,
            "title": title,
            "retrieved_at": datetime.now(UTC),
            "metadata": json.dumps(metadata),
        }
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    insert into sources (
                        security_id, source_type, source_uri, title,
                        retrieved_at, freshness, metadata
                    ) values (
                        null, :source_type, :source_uri, :title,
                        :retrieved_at, 'periodic', cast(:metadata as jsonb)
                    )
                    on conflict do nothing
                    returning id
                    """
                ),
                parameters,
            )
            source_id = result.scalar_one_or_none()
            if source_id is not None:
                return source_id

            source_id = await connection.scalar(
                text(
                    """
                    select id
                    from sources
                    where security_id is null
                      and source_uri = :source_uri
                      and published_at is null
                    order by retrieved_at desc
                    limit 1
                    """
                ),
                {"source_uri": source_uri},
            )
            if source_id is None:
                raise RuntimeError("Unable to resolve global official source")
            return source_id
