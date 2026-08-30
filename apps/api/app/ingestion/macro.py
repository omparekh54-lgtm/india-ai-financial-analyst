from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class MacroObservation:
    series_key: str
    observation_date: date
    value: int | float | Decimal | str
    unit: str | None = None
    released_at: datetime | None = None
    metadata: dict[str, object] = field(default_factory=dict)


_SERIES_ALIASES = {
    "repo": "repo_rate",
    "repo rate": "repo_rate",
    "policy repo rate": "repo_rate",
    "india 10y": "india_10y_yield",
    "india 10 year": "india_10y_yield",
    "india 10 year yield": "india_10y_yield",
    "10y gsec": "india_10y_yield",
    "usd inr": "usd_inr",
    "usdinr": "usd_inr",
    "brent": "brent",
    "brent crude": "brent",
    "india vix": "india_vix",
    "cpi yoy": "cpi_yoy",
    "cpi inflation": "cpi_yoy",
    "iip yoy": "iip_yoy",
    "iip growth": "iip_yoy",
    "fii cash net": "fii_cash_net_cr",
    "fpi cash net": "fii_cash_net_cr",
    "fii net investment": "fii_cash_net_cr",
    "dii cash net": "dii_cash_net_cr",
    "dii net investment": "dii_cash_net_cr",
}


class MacroObservationIngestor:
    """Stores normalized RBI/FRED/NSDL/exchange macro observations idempotently."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def ingest_batch(
        self,
        observations: list[MacroObservation],
        *,
        source_id: UUID | None = None,
    ) -> dict[str, int]:
        normalized = [normalize_macro_observation(item) for item in observations]
        async with self.engine.begin() as connection:
            for item in normalized:
                parameters = {
                    "series_key": item.series_key,
                    "observation_date": item.observation_date,
                    "value": item.value,
                    "unit": item.unit,
                    "source_id": source_id,
                    "released_at": item.released_at,
                    "metadata": json.dumps(item.metadata),
                }
                result = await connection.execute(
                    text(
                        """
                        insert into macro_observations (
                            series_key, observation_date, value, unit,
                            source_id, released_at, metadata
                        ) values (
                            :series_key, :observation_date, :value, :unit,
                            :source_id, :released_at, cast(:metadata as jsonb)
                        )
                        on conflict do nothing
                        returning id
                        """
                    ),
                    parameters,
                )
                if result.scalar_one_or_none() is not None:
                    continue
                await connection.execute(
                    text(
                        """
                        update macro_observations
                        set value = :value,
                            unit = :unit,
                            released_at = :released_at,
                            metadata = cast(:metadata as jsonb)
                        where series_key = :series_key
                          and observation_date = :observation_date
                          and source_id is not distinct from :source_id
                        """
                    ),
                    parameters,
                )
        return {"input_count": len(observations), "normalized_count": len(normalized)}


def normalize_macro_observation(observation: MacroObservation) -> MacroObservation:
    series_key = canonical_macro_series_key(observation.series_key)
    value = _decimal(observation.value)
    return MacroObservation(
        series_key=series_key,
        observation_date=observation.observation_date,
        value=value,
        unit=observation.unit.strip() if observation.unit else None,
        released_at=observation.released_at,
        metadata=dict(observation.metadata),
    )


def canonical_macro_series_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()
    if not normalized:
        raise ValueError("macro series key cannot be empty")
    return _SERIES_ALIASES.get(normalized, normalized.replace(" ", "_"))


def _decimal(value: int | float | Decimal | str) -> Decimal:
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"invalid macro value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("macro value must be finite")
    return result
