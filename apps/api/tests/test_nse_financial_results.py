import pytest

from app.connectors.nse_financial_results import (
    dedupe_financial_result_records,
    normalize_period,
    normalize_xbrl_url,
    parse_nse_financial_results,
)


def test_parse_nse_financial_results_normalizes_current_fields() -> None:
    records = parse_nse_financial_results(
        [
            {
                "symbol": "RELIANCE",
                "period": "Quarterly",
                "relatingTo": "30-Jun-2026",
                "financialYear": "2026-2027",
                "fromDate": "01-04-2026",
                "toDate": "30-06-2026",
                "filingDate": "15-07-2026 18:42:10",
                "broadCastDate": "15-07-2026 18:43:00",
                "consolidated": "Consolidated",
                "bank": "",
                "xbrl": (
                    "https://nsearchives.nseindia.com/corporate/ixbrl/"
                    "INTEGRATED_FILING_INDAS_123_15072026184210_iXBRL_WEB.html"
                ),
            }
        ],
        expected_symbol="reliance",
        expected_period="quarterly",
    )

    assert len(records) == 1
    record = records[0]
    assert record.symbol == "RELIANCE"
    assert record.period == "Quarterly"
    assert record.period_start is not None and record.period_start.isoformat() == "2026-04-01"
    assert record.period_end is not None and record.period_end.isoformat() == "2026-06-30"
    assert record.filing_at is not None
    assert record.consolidation == "Consolidated"
    assert record.xbrl_url.startswith("https://nsearchives.nseindia.com/")


def test_parse_nse_financial_results_accepts_schema_aliases_and_data_wrapper() -> None:
    records = parse_nse_financial_results(
        {
            "data": [
                {
                    "smSymbol": "HDFCBANK",
                    "periodType": "Annual",
                    "financial_year": "2025-2026",
                    "periodStart": "2025-04-01",
                    "periodEnd": "2026-03-31",
                    "xbrlUrl": "/corporate/ixbrl/HDFCBANK_20260331_iXBRL_WEB.html",
                    "bankFlag": "B",
                }
            ]
        },
        expected_symbol="HDFCBANK",
        expected_period="Annual",
    )

    assert len(records) == 1
    assert records[0].bank_flag == "B"
    assert records[0].xbrl_url == (
        "https://www.nseindia.com/corporate/ixbrl/HDFCBANK_20260331_iXBRL_WEB.html"
    )


def test_parse_nse_financial_results_rejects_symbol_mismatch() -> None:
    with pytest.raises(ValueError, match="symbol mismatch"):
        parse_nse_financial_results(
            [
                {
                    "symbol": "TCS",
                    "period": "Quarterly",
                    "xbrl": "https://nsearchives.nseindia.com/corporate/tcs.html",
                }
            ],
            expected_symbol="INFY",
            expected_period="Quarterly",
        )


def test_parser_skips_other_period_and_rows_without_xbrl() -> None:
    records = parse_nse_financial_results(
        [
            {"symbol": "INFY", "period": "Annual", "xbrl": None},
            {
                "symbol": "INFY",
                "period": "Annual",
                "xbrl": "https://nsearchives.nseindia.com/corporate/annual.html",
            },
            {
                "symbol": "INFY",
                "period": "Quarterly",
                "xbrl": "https://nsearchives.nseindia.com/corporate/quarterly.html",
            },
        ],
        expected_symbol="INFY",
        expected_period="Annual",
    )

    assert [record.xbrl_url for record in records] == [
        "https://nsearchives.nseindia.com/corporate/annual.html"
    ]


def test_normalize_xbrl_url_rejects_external_or_insecure_hosts() -> None:
    with pytest.raises(ValueError, match="official NSE HTTPS host"):
        normalize_xbrl_url("https://evil.example/result.xml")
    with pytest.raises(ValueError, match="official NSE HTTPS host"):
        normalize_xbrl_url("http://nsearchives.nseindia.com/result.xml")
    assert normalize_xbrl_url("-") is None


def test_dedupe_financial_results_uses_one_record_per_xbrl_url() -> None:
    records = parse_nse_financial_results(
        [
            {
                "symbol": "TCS",
                "period": "Quarterly",
                "toDate": "30-06-2026",
                "filingDate": "10-07-2026 10:00:00",
                "xbrl": "https://nsearchives.nseindia.com/corporate/tcs-q1.html",
            },
            {
                "symbol": "TCS",
                "period": "Quarterly",
                "toDate": "30-06-2026",
                "filingDate": "10-07-2026 11:00:00",
                "xbrl": "https://nsearchives.nseindia.com/corporate/tcs-q1.html",
            },
        ],
        expected_symbol="TCS",
        expected_period="Quarterly",
    )

    deduped = dedupe_financial_result_records(records)
    assert len(deduped) == 1
    assert deduped[0].filing_at is not None
    assert deduped[0].filing_at.hour == 11


def test_period_validation_is_strict() -> None:
    assert normalize_period("quarterly") == "Quarterly"
    assert normalize_period("ANNUAL") == "Annual"
    with pytest.raises(ValueError, match="Quarterly or Annual"):
        normalize_period("half-year")
