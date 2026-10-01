from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.ingestion.nse_bhavcopy import full_delivery_url, parse_full_delivery

SESSION = date(2026, 9, 30)
IDENTITY = b"Company Name,Industry,Symbol,Series,ISIN Code\nCompany,Energy,TEST,EQ,INE000A01010\n"
HEADER = "SYMBOL, SERIES, DATE1, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, TTL_TRD_QNTY\n"
ROW = "TEST, EQ, 30-Sep-2026, 10, 12, 9, 11, 100\n"
TARGETS = {"TEST": "INE000A01010"}


def parse(rows: str = ROW, identity: bytes = IDENTITY):
    return parse_full_delivery((HEADER + rows).encode(), session=SESSION,
                               targets=TARGETS, identity_content=identity)


def test_official_delivery_identity_join_and_eod_session() -> None:
    assert full_delivery_url(SESSION).endswith("products/content/sec_bhavdata_full_30092026.csv")
    bar = parse()["TEST"]
    assert bar.close == Decimal(11)
    assert bar.ts == datetime(2026, 9, 29, 18, 30, tzinfo=UTC)
    assert bar.provider == "nse" and not bar.is_adjusted


@pytest.mark.parametrize("identity", [
    IDENTITY.replace(b"INE000A01010", b"INE000B01010"),
    IDENTITY.replace(b"TEST", b"OTHER"),
    IDENTITY.replace(b",EQ,", b",BE,"),
    IDENTITY + IDENTITY.split(b"\n")[1] + b"\n",
])
def test_official_identity_failures_prevent_price_import(identity: bytes) -> None:
    with pytest.raises(ValueError):
        parse(identity=identity)


@pytest.mark.parametrize("rows", [
    ROW + ROW,
    ROW.replace("30-Sep", "29-Sep"),
    ROW.replace("EQ", "BE"),
    ROW.replace("12", "8"),
    ROW.replace("11", "NaN"),
    ROW.replace("11", "0"),
    ROW.replace("100", "Infinity"),
])
def test_delivery_rows_fail_closed(rows: str) -> None:
    with pytest.raises(ValueError):
        parse(rows=rows)


def test_missing_delivery_columns_are_rejected() -> None:
    with pytest.raises(ValueError, match="columns"):
        parse_full_delivery(b"SYMBOL\nTEST\n", session=SESSION,
                            targets=TARGETS, identity_content=IDENTITY)
