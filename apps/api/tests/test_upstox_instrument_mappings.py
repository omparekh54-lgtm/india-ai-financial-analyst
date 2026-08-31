import gzip
import json
from uuid import UUID

import pytest

from scripts.backfill_upstox_instrument_mappings import (
    CanonicalSecurity,
    UpstoxMappingRow,
    match_mappings,
    parse_mapping_rows,
    validate_mapping_rows,
)


def _artifact(rows: list[dict[str, object]], *, gzip_payload: bool = False) -> bytes:
    content = json.dumps(rows).encode("utf-8")
    return gzip.compress(content) if gzip_payload else content


def test_parse_mapping_rows_accepts_only_nse_cash_equities() -> None:
    content = _artifact(
        [
            {
                "segment": "NSE_EQ",
                "exchange": "NSE",
                "instrument_type": "EQ",
                "isin": "INE002A01018",
                "trading_symbol": "RELIANCE",
                "instrument_key": "NSE_EQ|INE002A01018",
                "exchange_token": "2885",
                "security_type": "NORMAL",
            },
            {
                "segment": "NSE_FO",
                "exchange": "NSE",
                "instrument_type": "FUT",
                "isin": "",
                "trading_symbol": "RELIANCE26AUGFUT",
                "instrument_key": "NSE_FO|123",
            },
            {
                "segment": "BSE_EQ",
                "exchange": "BSE",
                "instrument_type": "EQ",
                "isin": "INE002A01018",
                "trading_symbol": "RELIANCE",
                "instrument_key": "BSE_EQ|500325",
            },
        ],
        gzip_payload=True,
    )

    rows = parse_mapping_rows(content)

    assert rows == [
        UpstoxMappingRow(
            isin="INE002A01018",
            trading_symbol="RELIANCE",
            instrument_key="NSE_EQ|INE002A01018",
            exchange_token="2885",
            security_type="NORMAL",
        )
    ]


def test_validate_mapping_rows_rejects_duplicate_isin_or_instrument_key() -> None:
    base = UpstoxMappingRow(
        isin="INE002A01018",
        trading_symbol="RELIANCE",
        instrument_key="NSE_EQ|INE002A01018",
        exchange_token="2885",
        security_type="NORMAL",
    )
    duplicate_isin = UpstoxMappingRow(
        isin=base.isin,
        trading_symbol="RELIANCE2",
        instrument_key="NSE_EQ|OTHER",
        exchange_token="9999",
        security_type="NORMAL",
    )
    with pytest.raises(ValueError, match="duplicate identifiers"):
        validate_mapping_rows([base, duplicate_isin] * 500, min_rows=1000)

    rows = [
        UpstoxMappingRow(
            isin=f"IN{i:010d}"[-12:],
            trading_symbol=f"SYM{i}",
            instrument_key=f"NSE_EQ|KEY{i}",
            exchange_token=str(i),
            security_type="NORMAL",
        )
        for i in range(1000)
    ]
    rows[-1] = UpstoxMappingRow(
        isin=rows[-1].isin,
        trading_symbol=rows[-1].trading_symbol,
        instrument_key=rows[0].instrument_key,
        exchange_token=rows[-1].exchange_token,
        security_type="NORMAL",
    )
    with pytest.raises(ValueError, match="duplicate identifiers"):
        validate_mapping_rows(rows, min_rows=1000)


def test_validate_mapping_rows_enforces_production_floor() -> None:
    with pytest.raises(ValueError, match="min_rows must be >= 1000"):
        validate_mapping_rows([], min_rows=999)

    with pytest.raises(ValueError, match="contains only"):
        validate_mapping_rows(
            [
                UpstoxMappingRow(
                    isin="INE002A01018",
                    trading_symbol="RELIANCE",
                    instrument_key="NSE_EQ|INE002A01018",
                    exchange_token="2885",
                    security_type="NORMAL",
                )
            ],
            min_rows=1000,
        )


def test_match_mappings_uses_exact_isin_and_preserves_canonical_identity() -> None:
    canonical = [
        CanonicalSecurity(
            security_id=UUID("11111111-1111-1111-1111-111111111111"),
            isin="INE002A01018",
            nse_symbol="RELIANCE",
        ),
        CanonicalSecurity(
            security_id=UUID("22222222-2222-2222-2222-222222222222"),
            isin="INE467B01029",
            nse_symbol="TCS",
        ),
    ]
    mappings = [
        UpstoxMappingRow(
            isin="INE002A01018",
            trading_symbol="RELIANCE",
            instrument_key="NSE_EQ|INE002A01018",
            exchange_token="2885",
            security_type="NORMAL",
        ),
        UpstoxMappingRow(
            isin="INE467B01029",
            trading_symbol="TCS-EQ",
            instrument_key="NSE_EQ|INE467B01029",
            exchange_token="11536",
            security_type="NORMAL",
        ),
    ]

    matched, missing = match_mappings(canonical, mappings)

    assert missing == []
    assert [security.security_id for security, _ in matched] == [
        canonical[0].security_id,
        canonical[1].security_id,
    ]
    assert matched[1][0].nse_symbol == "TCS"
    assert matched[1][1].trading_symbol == "TCS-EQ"
    assert canonical[1].nse_symbol == "TCS"


def test_match_mappings_reports_missing_without_symbol_fallback() -> None:
    canonical = [
        CanonicalSecurity(
            security_id=UUID("33333333-3333-3333-3333-333333333333"),
            isin="INE000000001",
            nse_symbol="SAME",
        )
    ]
    mappings = [
        UpstoxMappingRow(
            isin="INE999999999",
            trading_symbol="SAME",
            instrument_key="NSE_EQ|INE999999999",
            exchange_token="1",
            security_type="NORMAL",
        )
    ]

    matched, missing = match_mappings(canonical, mappings)

    assert matched == []
    assert missing == canonical
