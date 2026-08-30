from __future__ import annotations

import csv
import io
import json
from datetime import date

from app.ingestion.financials import RawFinancialFact, normalize_financial_fact
from app.ingestion.reference_files import ReferenceFileError


def parse_financial_csv(content: str, *, min_rows: int = 1) -> list[RawFinancialFact]:
    if min_rows < 1:
        raise ReferenceFileError("minimum financial rows must be >= 1")

    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise ReferenceFileError("financial CSV has no header")

    facts: list[RawFinancialFact] = []
    seen_keys: set[tuple[str, date, str]] = set()
    for index, raw in enumerate(reader, start=2):
        row = {
            str(key).strip().lower().replace(" ", "_"): str(value or "").strip()
            for key, value in raw.items()
        }
        name = row.get("fact_name") or row.get("name") or ""
        period_end = row.get("period_end") or ""
        period_type = row.get("period_type") or ""
        value = row.get("value") or ""
        if not name or not period_end or not period_type or not value:
            raise ReferenceFileError(
                "invalid financial CSV row "
                f"{index}: fact_name/period_end/period_type/value are required"
            )

        try:
            metadata = _metadata(row.get("metadata_json"), row=index)
            fact = RawFinancialFact(
                name=name,
                period_start=_optional_date(row.get("period_start")),
                period_end=date.fromisoformat(period_end[:10]),
                period_type=period_type,
                value=value,
                unit=(row.get("unit") or "").strip() or None,
                metadata=metadata,
            )
            normalized = normalize_financial_fact(fact)
        except (TypeError, ValueError) as exc:
            raise ReferenceFileError(f"invalid financial CSV row {index}: {exc}") from exc

        natural_key = (
            normalized.fact_name,
            normalized.period_end,
            normalized.period_type,
        )
        if natural_key in seen_keys:
            raise ReferenceFileError(
                "duplicate canonical financial fact at row "
                f"{index}: {normalized.fact_name}/{normalized.period_end.isoformat()}/"
                f"{normalized.period_type}"
            )
        seen_keys.add(natural_key)
        facts.append(fact)

    if len(facts) < min_rows:
        raise ReferenceFileError(
            f"financial CSV contains only {len(facts)} rows; minimum expected is {min_rows}"
        )
    return facts


def _optional_date(value: str | None) -> date | None:
    cleaned = (value or "").strip()
    return date.fromisoformat(cleaned[:10]) if cleaned else None


def _metadata(value: str | None, *, row: int) -> dict[str, object]:
    cleaned = (value or "").strip()
    if not cleaned:
        return {"source_format": "approved_csv"}
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata_json on row {row} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"metadata_json on row {row} must be a JSON object")
    return {"source_format": "approved_csv", **parsed}
