from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.base import SourceEnvelope
from app.ingestion.macro import MacroObservation, MacroObservationIngestor


@dataclass(frozen=True)
class FredMacroSeries:
    series_key: str
    series_id: str
    title: str
    unit: str


FRED_MACRO_SERIES: dict[str, FredMacroSeries] = {
    "usd_inr": FredMacroSeries(
        series_key="usd_inr",
        series_id="DEXINUS",
        title="Indian Rupees to U.S. Dollar Spot Exchange Rate",
        unit="INR per USD",
    ),
    "brent": FredMacroSeries(
        series_key="brent",
        series_id="DCOILBRENTEU",
        title="Crude Oil Prices: Brent - Europe",
        unit="USD per barrel",
    ),
}


@dataclass(frozen=True)
class ParsedFredSeries:
    series: FredMacroSeries
    observations: tuple[MacroObservation, ...]
    skipped_missing: int

    @property
    def first_observation_date(self) -> date:
        return self.observations[0].observation_date

    @property
    def last_observation_date(self) -> date:
        return self.observations[-1].observation_date


def parse_fred_series(
    envelope: SourceEnvelope,
    series: FredMacroSeries,
    *,
    min_rows: int = 2,
) -> ParsedFredSeries:
    if min_rows < 1:
        raise ValueError("min_rows must be >= 1")
    expected_uri = f"fred:{series.series_id}"
    if envelope.source_uri != expected_uri:
        raise ValueError(
            f"FRED source URI mismatch: expected {expected_uri}, got {envelope.source_uri}"
        )

    raw_observations = envelope.payload.get("observations")
    if not isinstance(raw_observations, list):
        raise TypeError("FRED payload does not contain an observations list")

    observations: list[MacroObservation] = []
    seen_dates: set[date] = set()
    skipped_missing = 0
    for raw in raw_observations:
        if not isinstance(raw, dict):
            raise TypeError("FRED observations must be JSON objects")
        raw_date = str(raw.get("date") or "").strip()
        raw_value = str(raw.get("value") or "").strip()
        if not raw_date:
            raise ValueError("FRED observation is missing a date")
        try:
            observation_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError(f"Invalid FRED observation date: {raw_date!r}") from exc
        if observation_date in seen_dates:
            raise ValueError(f"Duplicate FRED observation date: {observation_date.isoformat()}")
        seen_dates.add(observation_date)

        if raw_value in {"", "."}:
            skipped_missing += 1
            continue
        try:
            value = Decimal(raw_value)
        except InvalidOperation as exc:
            raise ValueError(
                f"Invalid FRED value for {observation_date.isoformat()}: {raw_value!r}"
            ) from exc
        if not value.is_finite():
            raise ValueError("FRED observation value must be finite")

        metadata: dict[str, object] = {
            "provider": "fred",
            "fred_series_id": series.series_id,
        }
        for key in ("realtime_start", "realtime_end"):
            value_metadata = raw.get(key)
            if value_metadata:
                metadata[key] = str(value_metadata)
        observations.append(
            MacroObservation(
                series_key=series.series_key,
                observation_date=observation_date,
                value=value,
                unit=series.unit,
                metadata=metadata,
            )
        )

    observations.sort(key=lambda item: item.observation_date)
    if len(observations) < min_rows:
        raise ValueError(
            f"FRED series {series.series_id} contains only {len(observations)} usable rows; "
            f"minimum expected is {min_rows}"
        )
    return ParsedFredSeries(
        series=series,
        observations=tuple(observations),
        skipped_missing=skipped_missing,
    )


async def ingest_fred_series(
    engine: AsyncEngine,
    envelope: SourceEnvelope,
    series: FredMacroSeries,
    *,
    min_rows: int = 2,
) -> dict[str, object]:
    parsed = parse_fred_series(envelope, series, min_rows=min_rows)
    source_id = await _upsert_stable_fred_source(engine, envelope, parsed)
    ingestion = await MacroObservationIngestor(engine).ingest_batch(
        list(parsed.observations),
        source_id=source_id,
    )
    return {
        "series_key": series.series_key,
        "series_id": series.series_id,
        "source_id": str(source_id),
        "row_count": len(parsed.observations),
        "skipped_missing": parsed.skipped_missing,
        "first_observation_date": parsed.first_observation_date.isoformat(),
        "last_observation_date": parsed.last_observation_date.isoformat(),
        "ingestion": ingestion,
    }


async def _upsert_stable_fred_source(
    engine: AsyncEngine,
    envelope: SourceEnvelope,
    parsed: ParsedFredSeries,
) -> UUID:
    metadata = {
        **envelope.metadata,
        "provider": "fred",
        "fred_series_id": parsed.series.series_id,
        "series_key": parsed.series.series_key,
        "unit": parsed.series.unit,
        "row_count": len(parsed.observations),
        "skipped_missing": parsed.skipped_missing,
        "first_observation_date": parsed.first_observation_date.isoformat(),
        "last_observation_date": parsed.last_observation_date.isoformat(),
    }
    parameters = {
        "source_uri": envelope.source_uri,
        "title": parsed.series.title,
        "retrieved_at": envelope.retrieved_at,
        "metadata": json.dumps(metadata),
    }
    async with engine.begin() as connection:
        result = await connection.execute(
            text(
                """
                insert into sources (
                    security_id, source_type, source_uri, title, published_at,
                    retrieved_at, freshness, metadata
                ) values (
                    null, 'macro_observation', :source_uri, :title, null,
                    :retrieved_at, 'periodic', cast(:metadata as jsonb)
                )
                on conflict do nothing
                returning id
                """
            ),
            parameters,
        )
        source_id = result.scalar_one_or_none()
        if source_id is None:
            source_id = await connection.scalar(
                text(
                    """
                    select id
                    from sources
                    where security_id is null
                      and source_uri = :source_uri
                      and published_at is null
                    limit 1
                    """
                ),
                parameters,
            )
        if source_id is None:
            raise RuntimeError("Unable to resolve stable FRED source")
        await connection.execute(
            text(
                """
                update sources
                set source_type = 'macro_observation',
                    title = :title,
                    retrieved_at = :retrieved_at,
                    freshness = 'periodic',
                    metadata = cast(:metadata as jsonb)
                where id = :source_id
                """
            ),
            {**parameters, "source_id": source_id},
        )
    return source_id
