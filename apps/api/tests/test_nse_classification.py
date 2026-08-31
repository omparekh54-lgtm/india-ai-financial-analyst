from __future__ import annotations

import pytest

from app.connectors.nse_classification import parse_nse_quote_classification


def _payload() -> dict[str, object]:
    return {
        "info": {
            "symbol": "HDFCBANK",
            "isin": "INE040A01034",
        },
        "industryInfo": {
            "macro": "Financial Services",
            "sector": "Financial Services",
            "industry": "Banks",
            "basicIndustry": "Private Sector Bank",
        },
    }


def test_parse_nse_quote_classification_preserves_all_four_levels() -> None:
    classification = parse_nse_quote_classification(
        _payload(),
        expected_symbol="HDFCBANK",
        expected_isin="INE040A01034",
    )

    assert classification.symbol == "HDFCBANK"
    assert classification.isin == "INE040A01034"
    assert classification.macro_sector == "Financial Services"
    assert classification.sector == "Financial Services"
    assert classification.industry == "Banks"
    assert classification.basic_industry == "Private Sector Bank"
    assert classification.source_uri.endswith("symbol=HDFCBANK")


def test_parse_nse_quote_classification_rejects_isin_mismatch() -> None:
    with pytest.raises(ValueError, match="ISIN mismatch"):
        parse_nse_quote_classification(
            _payload(),
            expected_symbol="HDFCBANK",
            expected_isin="INE000000000",
        )


def test_parse_nse_quote_classification_rejects_incomplete_taxonomy() -> None:
    payload = _payload()
    payload["industryInfo"] = {
        "macro": "Financial Services",
        "sector": "Financial Services",
        "industry": "Banks",
        "basicIndustry": "N/A",
    }

    with pytest.raises(ValueError, match="basicIndustry"):
        parse_nse_quote_classification(
            payload,
            expected_symbol="HDFCBANK",
            expected_isin="INE040A01034",
        )


def test_source_uri_encodes_special_symbols() -> None:
    payload = {
        "info": {"symbol": "M&M", "isin": "INE101A01026"},
        "industryInfo": {
            "macro": "Consumer Discretionary",
            "sector": "Automobile and Auto Components",
            "industry": "Automobiles",
            "basicIndustry": "Passenger Cars & Utility Vehicles",
        },
    }
    classification = parse_nse_quote_classification(
        payload,
        expected_symbol="M&M",
        expected_isin="INE101A01026",
    )

    assert classification.source_uri.endswith("symbol=M%26M")
