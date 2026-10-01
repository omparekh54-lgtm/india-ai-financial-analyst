"""Strict NSE UDiFF EQ EOD input parsing, matched to canonical symbol and ISIN."""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.connectors.nse_sectoral_indices import parse_sectoral_index_csv
from app.ingestion.market import MarketBarInput, normalize_market_bar

MAX_FILE_BYTES = 25 * 1024 * 1024


def bhavcopy_url(session: date) -> str:
    return ("https://nsearchives.nseindia.com/content/cm/"
            f"BhavCopy_NSE_CM_0_0_0_{session:%Y%m%d}_F_0000.csv.zip")


def full_delivery_url(session: date) -> str:
    return ("https://nsearchives.nseindia.com/products/content/"
            f"sec_bhavdata_full_{session:%d%m%Y}.csv")


def parse_full_delivery(content: bytes, *, session: date, targets: dict[str, str],
                        identity_content: bytes) -> dict[str, MarketBarInput]:
    """Join the report's symbol to a fresh official constituent-file ISIN.

    The delivery report has no ISIN column. Both original files are required and
    recorded; a symbol-only match to an unchecked local master is insufficient.
    """
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("Invalid full-delivery report size")
    if not identity_content or len(identity_content) > MAX_FILE_BYTES:
        raise ValueError("Invalid official identity report size")
    identity_reader = csv.DictReader(io.StringIO(identity_content.decode("utf-8-sig")))
    if not {"Symbol", "ISIN Code", "Series"}.issubset(identity_reader.fieldnames or []):
        raise ValueError("Official identity columns are missing")
    seen_symbols, seen_isins = set(), set()
    for row in identity_reader:
        symbol, isin = row["Symbol"].strip().upper(), row["ISIN Code"].strip().upper()
        if row["Series"].strip().upper() != "EQ" or not symbol or not isin:
            raise ValueError("Official identity must be an EQ security")
        if symbol in seen_symbols or isin in seen_isins:
            raise ValueError("Duplicate official constituent identity")
        seen_symbols.add(symbol)
        seen_isins.add(isin)
    entries = parse_sectoral_index_csv(identity_content.decode("utf-8-sig"))
    identities: dict[str, str] = {}
    for entry in entries:
        if entry.symbol in identities:
            raise ValueError("Ambiguous official symbol identity")
        identities[entry.symbol] = entry.isin
    for symbol, isin in targets.items():
        if identities.get(symbol) != isin.upper():
            raise ValueError(f"Official constituent ISIN mismatch for {symbol}")
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    reader.fieldnames = [name.strip() for name in (reader.fieldnames or [])]
    required = {"SYMBOL", "SERIES", "DATE1", "OPEN_PRICE", "HIGH_PRICE",
                "LOW_PRICE", "CLOSE_PRICE", "TTL_TRD_QNTY"}
    if not required.issubset(reader.fieldnames):
        raise ValueError("Full-delivery report columns are missing")
    output = {}
    for row in reader:
        symbol = row["SYMBOL"].strip().upper()
        if symbol not in targets or row["SERIES"].strip().upper() != "EQ":
            continue
        row_date = datetime.strptime(row["DATE1"].strip(), "%d-%b-%Y").replace(
            tzinfo=ZoneInfo("Asia/Kolkata"),
        ).date()
        if row_date != session:
            raise ValueError("Full-delivery row has a different trading date")
        if symbol in output:
            raise ValueError(f"Ambiguous full-delivery EQ row for {symbol}")
        bar = MarketBarInput(
            ts=datetime.combine(session, time.min, ZoneInfo("Asia/Kolkata")).astimezone(UTC),
            open=Decimal(row["OPEN_PRICE"]), high=Decimal(row["HIGH_PRICE"]),
            low=Decimal(row["LOW_PRICE"]), close=Decimal(row["CLOSE_PRICE"]),
            volume=Decimal(row["TTL_TRD_QNTY"]), provider="nse", interval="1d",
            is_adjusted=False,
        )
        _validate_nse_bar(bar)
        output[symbol] = bar
    if set(output) != set(targets):
        raise ValueError("Full-delivery report does not cover every requested security")
    return output


def parse_bhavcopy(content: bytes, *, session: date,
                   targets: dict[str, str]) -> dict[str, MarketBarInput]:
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("Invalid bhavcopy archive size")
    expected_name = f"BhavCopy_NSE_CM_0_0_0_{session:%Y%m%d}_F_0000.csv"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if (len(entries) != 1 or entries[0].filename != expected_name
                or entries[0].file_size > MAX_FILE_BYTES):
            raise ValueError("Unexpected or oversized bhavcopy archive member")
        text = archive.read(entries[0]).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    required = {"TradDt", "ISIN", "TckrSymb", "SctySrs", "OpnPric", "HghPric",
                "LwPric", "ClsPric", "TtlTradgVol"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("NSE UDiFF required columns are missing")
    output = {}
    for row in reader:
        symbol = row["TckrSymb"].strip().upper()
        if symbol not in targets or row["SctySrs"].strip().upper() != "EQ":
            continue
        if row["ISIN"].strip().upper() != targets[symbol].upper():
            raise ValueError(f"Bhavcopy ISIN mismatch for {symbol}")
        if date.fromisoformat(row["TradDt"].strip()) != session:
            raise ValueError("Bhavcopy row has a different trading date")
        if symbol in output:
            raise ValueError(f"Ambiguous EQ bhavcopy row for {symbol}")
        bar = MarketBarInput(
            ts=datetime.combine(session, time.min, ZoneInfo("Asia/Kolkata")).astimezone(UTC),
            open=Decimal(row["OpnPric"]), high=Decimal(row["HghPric"]),
            low=Decimal(row["LwPric"]), close=Decimal(row["ClsPric"]),
            volume=Decimal(row["TtlTradgVol"]), provider="nse", interval="1d",
            is_adjusted=False,
        )
        _validate_nse_bar(bar)
        output[symbol] = bar
    if set(output) != set(targets):
        raise ValueError("Bhavcopy does not cover every requested security")
    return output


def _validate_nse_bar(bar: MarketBarInput) -> None:
    prices = [Decimal(str(value)) for value in (bar.open, bar.high, bar.low, bar.close)]
    if any(not value.is_finite() or value <= 0 for value in prices):
        raise ValueError("NSE OHLC prices must be finite and positive")
    if bar.volume is None or not Decimal(str(bar.volume)).is_finite():
        raise ValueError("NSE volume must be finite")
    normalize_market_bar(bar)
