import io
import zipfile
from datetime import date

import pytest

from app.ingestion.nse_bhavcopy import parse_bhavcopy

SESSION = date(2026, 9, 30)
HEADER = "TradDt,ISIN,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
ROW = "2026-09-30,INE002A01018,RELIANCE,EQ,100,110,95,105,1000\n"


def archive(text):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr("BhavCopy_NSE_CM_0_0_0_20260930_F_0000.csv", text)
    return output.getvalue()


def test_official_eod_requires_matching_identity_and_preserves_ist_session():
    bars = parse_bhavcopy(archive(HEADER + ROW), session=SESSION,
                         targets={"RELIANCE": "INE002A01018"})
    assert bars["RELIANCE"].close == 105
    assert bars["RELIANCE"].ts.isoformat() == "2026-09-29T18:30:00+00:00"
    assert bars["RELIANCE"].is_adjusted is False


@pytest.mark.parametrize("body", [
    ROW.replace("INE002A01018", "INE018A01030"),
    ROW.replace("2026-09-30", "2026-09-29"), ROW + ROW,
    ROW.replace(",EQ,", ",BE,"), ROW.replace(",105,", ",115,"),
])
def test_bhavcopy_rejects_identity_date_duplicate_missing_and_invalid_prices(body):
    with pytest.raises(ValueError):
        parse_bhavcopy(archive(HEADER + body), session=SESSION,
                       targets={"RELIANCE": "INE002A01018"})


def test_bhavcopy_rejects_missing_columns_and_unexpected_archive_member():
    with pytest.raises(ValueError, match="columns"):
        parse_bhavcopy(archive("Ticker\nRELIANCE\n"), session=SESSION,
                       targets={"RELIANCE": "INE002A01018"})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr("../prices.csv", HEADER + ROW)
    with pytest.raises(ValueError, match="member"):
        parse_bhavcopy(output.getvalue(), session=SESSION,
                       targets={"RELIANCE": "INE002A01018"})
