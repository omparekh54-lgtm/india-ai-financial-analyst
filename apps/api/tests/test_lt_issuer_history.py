from decimal import Decimal

import pytest

from scripts.recover_lt_issuer_history import parse_reviewed_pdf, parse_reviewed_table

TABLE = """LARSEN & TOUBRO LIMITED
CIN: L99999MH1946PLC004768
STATEMENT OF CONSOLIDATED UNAUDITED FINANCIAL RESULTS
FOR THE QUARTER AND NINE MONTHS ENDED DECEMBER 31, 2024
Crore
December 31, September 30, December 31, December 31, December 31, March 31,
2024 2024 2023 2024 2023 2024
a) Revenue from operations 64667.78 61554.58 55127.82 181342.18 154034.23 221112.91
b) Other income (net) 967.87 1101.27 837.75 2989.78 3116.29 4158.03
Total Income 65635.65 62655.85 55965.57 184331.96 157150.52 225270.94
c) Employee benefits expense 11912.19 11455.65 10253.27 34411.36 30441.50 41171.02
Total Expenses 60302.62 57100.76 51193.74 168767.19 143055.25 204847.44
5 Profit before tax (3+4) 5333.03 5555.09 4771.83 15564.77 14095.27 20517.11
Total tax expense 1332.00 1442.28 1177.32 4010.82 3529.09 4947.39
9 Net profit after tax and share in profit/(loss) of joint ventures/associates (7+8) 3973.98 4098.84 3592.84 11517.51 10533.93 15547.10
(a) Basic EPS ( ) 24.43 24.69 21.44 69.38 62.11 93.96
(b) Diluted EPS ( ) 24.41 24.68 21.42 69.33 62.05 93.88
"""


def test_reviewed_table_preserves_quarter_columns_units_and_reported_values():
    facts = parse_reviewed_table(TABLE)
    assert len(facts) == 20
    revenue = [f for f in facts if f.name == "revenue"]
    assert [f.value for f in revenue] == [Decimal("64667.78"), Decimal("61554.58")]
    assert [str(f.period_start) for f in revenue] == ["2024-10-01", "2024-07-01"]
    assert all(f.period_type == "quarterly" and f.unit == "INR crore" for f in revenue)
    assert all(f.unit == "INR/share" for f in facts if f.name.startswith("eps_"))
    assert all(f.metadata["consolidation"] == "consolidated" for f in facts)


@pytest.mark.parametrize("old,new", [
    ("L99999MH1946PLC004768", "WRONG_ISSUER"),
    ("CONSOLIDATED", "STANDALONE"),
    ("2024 2024 2023 2024 2023 2024", "2024 2023 2024 2024 2023 2024"),
    ("Crore", "Million"),
    ("64667.78", "64667.79"),
    ("60302.62", "60302.63"),
    ("24.43", "NaN"),
])
def test_table_rejects_issuer_column_unit_numeric_and_reconciliation_changes(old,new):
    with pytest.raises(ValueError):
        parse_reviewed_table(TABLE.replace(old,new))


def test_table_rejects_duplicate_and_missing_rows():
    with pytest.raises(ValueError):
        parse_reviewed_table(TABLE + "\n" + TABLE.splitlines()[7])
    with pytest.raises(ValueError):
        parse_reviewed_table(TABLE.replace("(a) Basic EPS", "(a) Missing EPS"))


def test_pdf_rejects_unreviewed_bytes_before_parsing():
    with pytest.raises(ValueError, match="checksum"):
        parse_reviewed_pdf(b"%PDF-unreviewed")
