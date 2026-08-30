from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.ingestion.macro import MacroObservation
from app.ingestion.market import MarketBarInput


class ReferenceFileError(ValueError):
    """Raised when an approved reference-data export cannot be normalized safely."""


def parse_benchmark_csv(
    content: str,
    *,
    provider: str,
    interval: str = "1d",
    timezone: str = "Asia/Kolkata",
) -> list[MarketBarInput]:
    provider = provider.strip().lower()
    interval = interval.strip().lower()
    if not provider:
        raise ReferenceFileError("benchmark provider cannot be empty")
    if not interval:
        raise ReferenceFileError("benchmark interval cannot be empty")
    try:
        tz = ZoneInfo(timezone)
    except Exception as exc:  # noqa: BLE001 - ZoneInfo raises implementation-specific lookup errors
        raise ReferenceFileError(f"unknown timezone: {timezone}") from exc

    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise ReferenceFileError("benchmark CSV has no header")
    bars: list[MarketBarInput] = []
    for index, raw in enumerate(reader, start=2):
        row = _normalized_row(raw)
        try:
            ts = _parse_timestamp(
                row.get("timestamp") or row.get("datetime") or row.get("date") or "",
                tz,
            )
            bars.append(
                MarketBarInput(
                    ts=ts,
                    open=_required_number(row, "open"),
                    high=_required_number(row, "high"),
                    low=_required_number(row, "low"),
                    close=_required_number(row, "close"),
                    volume=_optional_number(row.get("volume")),
                    provider=provider,
                    interval=interval,
                    is_adjusted=_bool_value(row.get("is_adjusted")),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ReferenceFileError(f"invalid benchmark CSV row {index}: {exc}") from exc
    if not bars:
        raise ReferenceFileError("benchmark CSV contains no usable rows")
    return bars


def parse_macro_csv(content: str) -> list[MacroObservation]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise ReferenceFileError("macro CSV has no header")
    observations: list[MacroObservation] = []
    for index, raw in enumerate(reader, start=2):
        row = _normalized_row(raw)
        series_key = (row.get("series_key") or row.get("series") or "").strip()
        observation_date = row.get("observation_date") or row.get("date") or ""
        value = (row.get("value") or "").strip()
        if not series_key or not observation_date or not value:
            raise ReferenceFileError(
                f"invalid macro CSV row {index}: series_key/date/value are required"
            )
        try:
            parsed_date = date.fromisoformat(observation_date[:10])
            released_at = _optional_datetime(row.get("released_at"))
        except ValueError as exc:
            raise ReferenceFileError(f"invalid macro CSV row {index}: {exc}") from exc
        observations.append(
            MacroObservation(
                series_key=series_key,
                observation_date=parsed_date,
                value=value,
                unit=(row.get("unit") or "").strip() or None,
                released_at=released_at,
                metadata={
                    "source_name": (row.get("source_name") or "").strip() or "approved_csv",
                },
            )
        )
    if not observations:
        raise ReferenceFileError("macro CSV contains no usable rows")
    return observations


def _normalized_row(raw: dict[str, str | None]) -> dict[str, str]:
    return {
        str(key).strip().lower().replace(" ", "_"): str(value or "").strip()
        for key, value in raw.items()
    }


def _parse_timestamp(value: str, timezone: ZoneInfo) -> datetime:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("timestamp/date is required")
    if len(cleaned) == 10:
        parsed = datetime.combine(date.fromisoformat(cleaned), datetime.min.time(), timezone)
    else:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(UTC)


def _optional_datetime(value: str | None) -> datetime | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_number(row: dict[str, str], key: str) -> float:
    value = row.get(key, "").replace(",", "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return float(value)


def _optional_number(value: str | None) -> float | None:
    cleaned = (value or "").replace(",", "").strip()
    return float(cleaned) if cleaned else None


def _bool_value(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y"}
