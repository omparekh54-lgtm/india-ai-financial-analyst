from __future__ import annotations

import json
from datetime import date

import pytest

from app.connectors.bse_public import (
    _build_page_url,
    _parse_bse_page,
    _validate_bse_api_url,
)
from app.connectors.india_official import parse_bse_disclosures


def test_bse_public_page_shape_normalizes_through_generic_parser() -> None:
    payload = json.dumps(
        {
            "Table": [
                {
                    "SCRIP_CD": "500209",
                    "SLONGNAME": "Infosys Ltd",
                    "NEWSSUB": "Investor Presentation",
                    "HEADLINE": "Presentation filed with the Exchange",
                    "NEWS_DT": "30-Aug-2026 19:20:00",
                    "ATTACHMENTNAME": (
                        "https://www.bseindia.com/xml-data/corpfiling/AttachLive/infosys.pdf"
                    ),
                }
            ],
            "Table1": [{"ROWCNT": "1"}],
        }
    ).encode()

    rows, total = _parse_bse_page(payload)
    normalized = json.dumps({"Table": rows}).encode()
    records = parse_bse_disclosures(
        normalized,
        "application/json",
        source_uri="https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
    )

    assert total == 1
    assert len(records) == 1
    assert records[0].bse_code == "500209"
    assert records[0].headline == "Investor Presentation"
    assert records[0].attachment_url and records[0].attachment_url.endswith("infosys.pdf")


def test_bse_public_url_is_bounded_to_observed_corporate_path() -> None:
    base = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
    _validate_bse_api_url(base)
    with pytest.raises(ValueError):
        _validate_bse_api_url("https://api.bseindia.com/BseIndiaAPI/api/MarketHighLow/w")
    with pytest.raises(ValueError):
        _validate_bse_api_url("https://example.com/BseIndiaAPI/api/AnnSubCategoryGetData/w")

    url = _build_page_url(
        base,
        page=2,
        start=date(2026, 8, 29),
        end=date(2026, 8, 30),
    )
    assert "pageno=2" in url
    assert "strPrevDate=29082026" in url
    assert "strToDate=30082026" in url
