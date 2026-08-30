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
from app.ingestion.exchange import ExchangeDisclosure, ExchangeDisclosureIngestor
from app.ingestion.macro import MacroObservationIngestor
from app.securities.repository import SecurityMasterRepository


class OfficialIndiaIngestionService:
    """Normalizes official NSE/BSE/RBI/NSDL payloads into provenance-aware storage."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.securities = SecurityMasterRepository(engine)
        self.exchange_ingestor = ExchangeDisclosureIngestor(engine)
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

        return {
            "exchange": exchange.strip().upper(),
            "parsed_count": len(records),
            "ingested_count": ingested,
            "unmatched_count": len(records) - ingested,
            "unmatched_identifiers": sorted(set(unmatched)),
            "event_types": event_types,
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
