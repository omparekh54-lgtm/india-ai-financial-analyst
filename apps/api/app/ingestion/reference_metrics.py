from __future__ import annotations

import csv
import io
import json
from datetime import date

from app.ingestion.metrics import SecurityMetricInput, normalize_security_metric
from app.ingestion.reference_files import ReferenceFileError


def parse_security_metrics_csv(
    content: str,
    *,
    min_rows: int = 1,
) -> list[SecurityMetricInput]:
    if min_rows < 1:
        raise ReferenceFileError("minimum security-metric rows must be >= 1")

    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    if reader.fieldnames is None:
        raise ReferenceFileError("security metrics CSV has no header")

    metrics: list[SecurityMetricInput] = []
    seen_keys: set[tuple[str, date]] = set()
    for index, raw in enumerate(reader, start=2):
        row = {
            str(key).strip().lower().replace(" ", "_"): str(value or "").strip()
            for key, value in raw.items()
        }
        metric_name = row.get("metric_name") or row.get("name") or ""
        as_of_date = row.get("as_of_date") or row.get("date") or ""
        value = row.get("value") or ""
        if not metric_name or not as_of_date or not value:
            raise ReferenceFileError(
                f"invalid security metrics CSV row {index}: metric_name/as_of_date/value are required"
            )

        try:
            metric = SecurityMetricInput(
                metric_name=metric_name,
                as_of_date=date.fromisoformat(as_of_date[:10]),
                value=value,
                unit=(row.get("unit") or "").strip() or None,
                metadata=_metadata(row.get("metadata_json"), row=index),
            )
            normalized = normalize_security_metric(metric)
        except (TypeError, ValueError) as exc:
            raise ReferenceFileError(f"invalid security metrics CSV row {index}: {exc}") from exc

        natural_key = (normalized.metric_name, normalized.as_of_date)
        if natural_key in seen_keys:
            raise ReferenceFileError(
                "duplicate canonical security metric at row "
                f"{index}: {normalized.metric_name}/{normalized.as_of_date.isoformat()}"
            )
        seen_keys.add(natural_key)
        metrics.append(metric)

    if len(metrics) < min_rows:
        raise ReferenceFileError(
            f"security metrics CSV contains only {len(metrics)} rows; minimum expected is {min_rows}"
        )
    return metrics


def _metadata(value: str | None, *, row: int) -> dict[str, object]:
    cleaned = (value or "").strip()
    if not cleaned:
        return {"source_format": "approved_csv"}
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata_json on row {row} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise TypeError(f"metadata_json on row {row} must be a JSON object")
    return {"source_format": "approved_csv", **parsed}
