from datetime import UTC, date, datetime

import pytest

from app.connectors.nse_financial_results import NseFinancialResultRecord
from app.ingestion.nse_financial_corpus import (
    financial_result_headline,
    financial_result_metadata,
    result_published_at,
    select_financial_result_records,
)


def _record(
    *,
    period_end: date | None,
    period: str = "Quarterly",
    consolidation: str | None = None,
    filing_at: datetime | None = None,
    broadcast_at: datetime | None = None,
    raw_index: int = 0,
    url_suffix: str = "result.xml",
) -> NseFinancialResultRecord:
    return NseFinancialResultRecord(
        symbol="RELIANCE",
        period=period,
        relating_to=period_end.isoformat() if period_end else None,
        financial_year="2026-27",
        period_start=None,
        period_end=period_end,
        filing_at=filing_at,
        broadcast_at=broadcast_at,
        consolidation=consolidation,
        bank_flag=None,
        xbrl_url=f"https://nsearchives.nseindia.com/corporate/{url_suffix}",
        raw_index=raw_index,
    )


def test_selector_prefers_consolidated_then_annual_for_same_period() -> None:
    period_end = date(2026, 3, 31)
    records = [
        _record(
            period_end=period_end,
            period="Annual",
            consolidation="Standalone",
            raw_index=0,
            url_suffix="standalone.xml",
        ),
        _record(
            period_end=period_end,
            period="Quarterly",
            consolidation="Consolidated",
            raw_index=1,
            url_suffix="consolidated-quarter.xml",
        ),
        _record(
            period_end=period_end,
            period="Annual",
            consolidation="Consolidated",
            raw_index=2,
            url_suffix="consolidated-annual.xml",
        ),
    ]

    selected = select_financial_result_records(records)

    assert len(selected) == 1
    assert selected[0].record.xbrl_url.endswith("consolidated-annual.xml")


def test_selector_returns_newest_distinct_periods_and_respects_limit() -> None:
    records = [
        _record(period_end=date(2026, 6, 30), url_suffix="q1.xml"),
        _record(period_end=date(2026, 3, 31), url_suffix="q4.xml"),
        _record(period_end=date(2025, 12, 31), url_suffix="q3.xml"),
    ]

    selected = select_financial_result_records(records, max_periods=2)

    assert [item.record.period_end for item in selected] == [
        date(2026, 6, 30),
        date(2026, 3, 31),
    ]


def test_missing_filing_timestamp_uses_conservative_period_end_not_retrieval_time() -> None:
    record = _record(period_end=date(2024, 3, 31))

    published_at, basis = result_published_at(record)

    assert published_at == datetime(2024, 3, 31, tzinfo=UTC)
    assert basis == "period_end_conservative_proxy"


def test_filing_timestamp_has_priority_and_is_normalized_to_utc() -> None:
    naive_filing = datetime(2026, 7, 20, 12, 30, tzinfo=UTC).replace(tzinfo=None)
    record = _record(
        period_end=date(2026, 6, 30),
        filing_at=naive_filing,
        broadcast_at=datetime(2026, 7, 20, 13, 0, tzinfo=UTC),
    )

    published_at, basis = result_published_at(record)

    assert published_at == datetime(2026, 7, 20, 12, 30, tzinfo=UTC)
    assert basis == "nse_filing_at"


def test_metadata_and_headline_preserve_source_context() -> None:
    selected = select_financial_result_records(
        [
            _record(
                period_end=date(2026, 6, 30),
                consolidation="Consolidated",
                filing_at=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
            )
        ]
    )[0]

    assert financial_result_headline(selected.record) == (
        "Financial results - RELIANCE - period ended 2026-06-30"
    )
    metadata = financial_result_metadata(selected)
    assert metadata["provider"] == "NSE"
    assert metadata["provenance_class"] == "official_source"
    assert metadata["production_approved"] is True
    assert metadata["timestamp_basis"] == "nse_filing_at"
    assert metadata["period_end"] == "2026-06-30"


def test_selector_rejects_invalid_limit_and_ignores_records_without_period_end() -> None:
    with pytest.raises(ValueError, match="max_periods"):
        select_financial_result_records([], max_periods=0)

    assert select_financial_result_records([_record(period_end=None)]) == ()
