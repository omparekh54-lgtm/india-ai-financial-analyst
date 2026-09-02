from datetime import date

import pytest

from app.connectors.nse_benchmarks import (
    normalize_benchmark_code,
    parse_nse_benchmark_history,
)


def test_normalize_benchmark_code_accepts_supported_aliases() -> None:
    assert normalize_benchmark_code("NIFTY 50") == "NIFTY50"
    assert normalize_benchmark_code("india vix") == "INDIAVIX"

    with pytest.raises(ValueError, match="NIFTY50 or INDIAVIX"):
        normalize_benchmark_code("SENSEX")


def test_parse_nifty_history_merges_turnover_volume() -> None:
    payload = {
        "data": {
            "indexCloseOnlineRecords": [
                {
                    "EOD_INDEX_NAME": "NIFTY 50",
                    "EOD_OPEN_INDEX_VAL": 25100.0,
                    "EOD_HIGH_INDEX_VAL": 25200.0,
                    "EOD_LOW_INDEX_VAL": 25050.0,
                    "EOD_CLOSE_INDEX_VAL": 25175.0,
                    "EOD_TIMESTAMP": "28-AUG-2026",
                },
                {
                    "EOD_INDEX_NAME": "NIFTY 50",
                    "EOD_OPEN_INDEX_VAL": "25,180.00",
                    "EOD_HIGH_INDEX_VAL": "25,260.00",
                    "EOD_LOW_INDEX_VAL": "25,140.00",
                    "EOD_CLOSE_INDEX_VAL": "25,230.00",
                    "EOD_TIMESTAMP": "31-AUG-2026",
                },
            ],
            "indexTurnoverRecords": [
                {
                    "HIT_TIMESTAMP": "28-AUG-2026",
                    "HIT_TRADED_QTY": 123456789,
                },
                {
                    "HIT_TIMESTAMP": "31-AUG-2026",
                    "HIT_TRADED_QTY": "130,000,000",
                },
            ],
        }
    }

    bars = parse_nse_benchmark_history(payload, benchmark_code="NIFTY50")

    assert len(bars) == 2
    assert bars[0].ts.date() == date(2026, 8, 28)
    assert bars[0].close == 25175.0
    assert bars[0].volume == 123456789.0
    assert bars[1].close == 25230.0
    assert bars[1].volume == 130000000.0
    assert bars[0].provider == "nse"


def test_parse_vix_history_accepts_list_response_without_volume() -> None:
    payload = [
        {
            "EOD_TIMESTAMP": "28-AUG-2026",
            "EOD_INDEX_NAME": "INDIA VIX",
            "EOD_OPEN_INDEX_VAL": 12.1,
            "EOD_HIGH_INDEX_VAL": 12.7,
            "EOD_LOW_INDEX_VAL": 11.9,
            "EOD_CLOSE_INDEX_VAL": 12.5,
        }
    ]

    bars = parse_nse_benchmark_history(payload, benchmark_code="INDIA VIX")

    assert len(bars) == 1
    assert bars[0].close == 12.5
    assert bars[0].volume is None


def test_parse_history_rejects_wrong_index_and_invalid_ohlc() -> None:
    wrong_name = [
        {
            "EOD_TIMESTAMP": "28-AUG-2026",
            "EOD_INDEX_NAME": "NIFTY BANK",
            "EOD_OPEN_INDEX_VAL": 100.0,
            "EOD_HIGH_INDEX_VAL": 110.0,
            "EOD_LOW_INDEX_VAL": 90.0,
            "EOD_CLOSE_INDEX_VAL": 105.0,
        }
    ]
    with pytest.raises(ValueError, match="name mismatch"):
        parse_nse_benchmark_history(wrong_name, benchmark_code="NIFTY50")

    invalid_ohlc = [
        {
            "EOD_TIMESTAMP": "28-AUG-2026",
            "EOD_INDEX_NAME": "INDIA VIX",
            "EOD_OPEN_INDEX_VAL": 12.0,
            "EOD_HIGH_INDEX_VAL": 11.0,
            "EOD_LOW_INDEX_VAL": 10.0,
            "EOD_CLOSE_INDEX_VAL": 12.5,
        }
    ]
    with pytest.raises(ValueError, match="high must be"):
        parse_nse_benchmark_history(invalid_ohlc, benchmark_code="INDIAVIX")
