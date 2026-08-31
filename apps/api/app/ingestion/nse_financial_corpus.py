from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time

from app.connectors.nse_financial_results import NseFinancialResultRecord


@dataclass(frozen=True)
class SelectedFinancialResult:
    record: NseFinancialResultRecord
    published_at: datetime
    timestamp_basis: str


def select_financial_result_records(
    records: list[NseFinancialResultRecord],
    *,
    max_periods: int = 10,
) -> tuple[SelectedFinancialResult, ...]:
    """Choose at most one authoritative XBRL result per period, newest period first.

    Consolidated results are preferred when available. At identical period ends, annual results
    are preferred over quarterly results because they usually expose a broader audited statement
    set. Missing filing timestamps fall back conservatively to period end, never retrieval time.
    """
    if max_periods < 1:
        raise ValueError("max_periods must be >= 1")

    by_period: dict[object, NseFinancialResultRecord] = {}
    for record in records:
        if record.period_end is None:
            continue
        existing = by_period.get(record.period_end)
        if existing is None or _preference_key(record) > _preference_key(existing):
            by_period[record.period_end] = record

    selected: list[SelectedFinancialResult] = []
    for period_end in sorted(by_period, reverse=True)[:max_periods]:
        record = by_period[period_end]
        published_at, basis = result_published_at(record)
        selected.append(
            SelectedFinancialResult(
                record=record,
                published_at=published_at,
                timestamp_basis=basis,
            )
        )
    return tuple(selected)


def result_published_at(record: NseFinancialResultRecord) -> tuple[datetime, str]:
    if record.filing_at is not None:
        return _utc(record.filing_at), "nse_filing_at"
    if record.broadcast_at is not None:
        return _utc(record.broadcast_at), "nse_broadcast_at"
    if record.period_end is None:
        raise ValueError("financial result requires period_end when filing timestamps are missing")
    return datetime.combine(record.period_end, time.min, tzinfo=UTC), "period_end_conservative_proxy"


def financial_result_headline(record: NseFinancialResultRecord) -> str:
    period = record.period_end.isoformat() if record.period_end is not None else "unknown period"
    return f"Financial results - {record.symbol} - period ended {period}"


def financial_result_metadata(
    selected: SelectedFinancialResult,
) -> dict[str, object]:
    record = selected.record
    return {
        "provider": "NSE",
        "provenance_class": "official_source",
        "production_approved": True,
        "document_type": "financial_results_xbrl",
        "period": record.period,
        "period_start": record.period_start.isoformat() if record.period_start else None,
        "period_end": record.period_end.isoformat() if record.period_end else None,
        "relating_to": record.relating_to,
        "financial_year": record.financial_year,
        "consolidation": record.consolidation,
        "bank_flag": record.bank_flag,
        "timestamp_basis": selected.timestamp_basis,
        "nse_raw_index": record.raw_index,
    }


def _preference_key(record: NseFinancialResultRecord) -> tuple[int, int, datetime, int]:
    return (
        _consolidation_rank(record.consolidation),
        1 if record.period.strip().lower() == "annual" else 0,
        _utc(record.filing_at or record.broadcast_at)
        if record.filing_at is not None or record.broadcast_at is not None
        else datetime.min.replace(tzinfo=UTC),
        -record.raw_index,
    )


def _consolidation_rank(value: str | None) -> int:
    normalized = (value or "").strip().lower()
    if normalized in {"c", "consolidated", "consol"} or "consolidat" in normalized:
        return 2
    if normalized in {"s", "standalone", "stand alone"} or "standalone" in normalized:
        return 1
    return 0


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
