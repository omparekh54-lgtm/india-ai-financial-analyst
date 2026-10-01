"""Strict NSE UDiFF EQ EOD input parsing, matched to canonical symbol and ISIN."""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.ingestion.market import MarketBarInput, normalize_market_bar

MAX_FILE_BYTES = 25 * 1024 * 1024


def bhavcopy_url(session: date) -> str:
    return ("https://nsearchives.nseindia.com/content/cm/"
            f"BhavCopy_NSE_CM_0_0_0_{session:%Y%m%d}_F_0000.csv.zip")


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
        normalize_market_bar(bar)
        output[symbol] = bar
    if set(output) != set(targets):
        raise ValueError("Bhavcopy does not cover every requested security")
    return output
