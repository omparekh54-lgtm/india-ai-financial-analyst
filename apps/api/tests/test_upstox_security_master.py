from __future__ import annotations

import gzip
import json

import pytest

from scripts.import_upstox_security_master import parse_equity_rows, parse_payload, validate_rows


def _documented_records() -> list[dict[str, object]]:
    return [
        {
            "segment": "NSE_EQ",
            "name": "RELIANCE INDUSTRIES LTD",
            "exchange": "NSE",
            "isin": "INE002A01018",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE002A01018",
            "exchange_token": "2885",
            "trading_symbol": "RELIANCE",
            "short_name": "Reliance Industries",
            "lot_size": 1,
            "tick_size": 5.0,
            "security_type": "NORMAL",
        },
        {
            "segment": "NSE_EQ",
            "name": "JOCIL LIMITED",
            "exchange": "NSE",
            "isin": "INE839G01010",
            "instrument_type": "EQ",
            "instrument_key": "NSE_EQ|INE839G01010",
            "exchange_token": "16927",
            "trading_symbol": "JOCIL",
            "short_name": "JOCIL",
            "lot_size": 1,
            "tick_size": 5.0,
            "security_type": "NORMAL",
        },
        {
            "segment": "BSE_INDEX",
            "name": "AUTO",
            "exchange": "BSE",
            "instrument_type": "INDEX",
            "instrument_key": "BSE_INDEX|AUTO",
            "exchange_token": "13",
            "trading_symbol": "AUTO",
        },
    ]


def test_parse_gzipped_upstox_bod_and_keep_only_nse_cash_equity() -> None:
    compressed = gzip.compress(json.dumps(_documented_records()).encode())
    payload = parse_payload(compressed)
    rows = parse_equity_rows(payload)

    assert [row["symbol"] for row in rows] == ["RELIANCE", "JOCIL"]
    assert rows[0]["isin"] == "INE002A01018"
    assert rows[0]["instrument_key"] == "NSE_EQ|INE002A01018"
    assert validate_rows(rows, min_rows=2)["row_count"] == 2


def test_upstox_master_rejects_duplicate_canonical_identifiers() -> None:
    rows = parse_equity_rows(_documented_records()[:2])
    duplicated = [*rows, dict(rows[0])]
    with pytest.raises(ValueError, match="duplicate identifiers"):
        validate_rows(duplicated, min_rows=2)


def test_upstox_master_enforces_minimum_real_universe_size() -> None:
    rows = parse_equity_rows(_documented_records())
    with pytest.raises(ValueError, match="minimum expected is 1000"):
        validate_rows(rows, min_rows=1000)
