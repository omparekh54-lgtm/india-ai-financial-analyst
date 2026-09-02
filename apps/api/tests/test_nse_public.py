from __future__ import annotations

import json

import pytest

from app.connectors.india_official import parse_nse_disclosures
from app.connectors.nse_public import _validate_nse_api_url, normalize_nse_announcement_json


def test_current_nse_api_shape_normalizes_to_generic_disclosure_schema() -> None:
    payload = json.dumps(
        [
            {
                "symbol": "INFY",
                "sm_name": "Infosys Limited",
                "desc": "Resignation of Director/KMP/SMP",
                "attchmntText": "The company has informed the Exchange about change in management.",
                "an_dt": "30-Aug-2026 19:15:00",
                "attchmntFile": "https://nsearchives.nseindia.com/corporate/INFY_TEST.pdf",
                "sm_isin": "INE009A01021",
                "seq_id": "12345",
            }
        ]
    ).encode()

    normalized = normalize_nse_announcement_json(payload)
    records = parse_nse_disclosures(
        normalized,
        "application/json",
        source_uri="https://www.nseindia.com/api/corporate-announcements?index=equities",
    )

    assert len(records) == 1
    assert records[0].nse_symbol == "INFY"
    assert records[0].company_name == "Infosys Limited"
    assert records[0].headline == "Resignation of Director/KMP/SMP"
    assert records[0].attachment_url == (
        "https://nsearchives.nseindia.com/corporate/INFY_TEST.pdf"
    )


def test_nse_public_fetcher_rejects_non_announcement_paths() -> None:
    _validate_nse_api_url("https://www.nseindia.com/api/corporate-announcements?index=equities")
    with pytest.raises(ValueError):
        _validate_nse_api_url("https://www.nseindia.com/api/marketStatus")
    with pytest.raises(ValueError):
        _validate_nse_api_url("https://example.com/api/corporate-announcements")
