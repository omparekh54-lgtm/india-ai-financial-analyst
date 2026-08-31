from datetime import date

from app.core.market_history_coverage import (
    MarketHistoryCoverageReport,
    SecurityMarketHistoryCoverage,
    parse_listing_date,
)
from app.core.market_history_policy import evaluate_market_history


def test_parse_listing_date_accepts_nse_and_iso_formats() -> None:
    assert parse_listing_date("29-NOV-1995") == date(1995, 11, 29)
    assert parse_listing_date("2026-08-31") == date(2026, 8, 31)
    assert parse_listing_date("31-08-2026") == date(2026, 8, 31)
    assert parse_listing_date("") is None
    assert parse_listing_date("NA") is None
    assert parse_listing_date("not-a-date") is None


def test_market_history_report_separates_incomplete_and_history_limited() -> None:
    as_of = date(2026, 8, 31)
    mature = SecurityMarketHistoryCoverage(
        security_id="1",
        symbol="MATURE",
        result=evaluate_market_history(
            listing_date=date(2020, 1, 1),
            as_of=as_of,
            bar_count=200,
            first_bar_date=date(2025, 11, 1),
            last_bar_date=as_of,
        ),
    )
    recent = SecurityMarketHistoryCoverage(
        security_id="2",
        symbol="RECENT",
        result=evaluate_market_history(
            listing_date=date(2026, 8, 21),
            as_of=as_of,
            bar_count=6,
            first_bar_date=date(2026, 8, 21),
            last_bar_date=as_of,
        ),
    )
    missing = SecurityMarketHistoryCoverage(
        security_id="3",
        symbol="MISSING",
        result=evaluate_market_history(
            listing_date=date(2020, 1, 1),
            as_of=as_of,
            bar_count=10,
            first_bar_date=date(2026, 8, 1),
            last_bar_date=as_of,
        ),
    )
    report = MarketHistoryCoverageReport(
        total_securities=3,
        complete_securities=2,
        technical_computable_securities=1,
        history_limited_recent_listings=1,
        securities=(mature, recent, missing),
    )

    assert report.complete is False
    assert report.incomplete_symbols == ("MISSING",)
    assert report.history_limited_symbols == ("RECENT",)
    payload = report.as_dict()
    assert payload["complete_coverage_pct"] == 66.67
    assert payload["history_limited_recent_listings"] == 1
