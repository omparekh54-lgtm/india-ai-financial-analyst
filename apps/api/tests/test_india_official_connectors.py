from datetime import UTC, datetime
from decimal import Decimal

from app.connectors.india_official import (
    BSE_CORPORATES_PAGE,
    MacroSeriesSpec,
    parse_bse_disclosures,
    parse_nsdl_flows,
    parse_nse_disclosures,
    parse_rbi_macro_series,
)


def test_nse_csv_announcement_is_normalized_with_ist_timestamp() -> None:
    payload = (
        b"SYMBOL,COMPANY NAME,SUBJECT,DETAILS,ATTACHMENT,BROADCAST DATE/TIME\n"
        b"INFY,Infosys Limited,Investor Presentation,Presentation filed,"
        b"https://nsearchives.nseindia.com/corporate/INFY_TEST.pdf,30-Aug-2026 18:00:00\n"
    )

    records = parse_nse_disclosures(payload, "text/csv")

    assert len(records) == 1
    assert records[0].nse_symbol == "INFY"
    assert records[0].headline == "Investor Presentation"
    assert records[0].published_at == datetime(2026, 8, 30, 12, 30, tzinfo=UTC)
    assert records[0].attachment_url == (
        "https://nsearchives.nseindia.com/corporate/INFY_TEST.pdf"
    )


def test_bse_json_announcement_is_normalized() -> None:
    payload = b'''{
      "Table": [{
        "SCRIP_CD": "532540",
        "SLONGNAME": "Tata Consultancy Services Ltd",
        "NEWSSUB": "Announcement under Regulation 30 - Analyst Meet",
        "HEADLINE": "Analyst meeting intimation",
        "NEWS_DT": "30-Aug-2026 17:45:00",
        "ATTACHMENTNAME": "https://www.bseindia.com/xml-data/corpfiling/AttachHis/test.pdf"
      }]
    }'''

    records = parse_bse_disclosures(payload, "application/json")

    assert len(records) == 1
    assert records[0].bse_code == "532540"
    assert records[0].company_name == "Tata Consultancy Services Ltd"
    assert records[0].published_at == datetime(2026, 8, 30, 12, 15, tzinfo=UTC)


def test_bse_mobile_html_extracts_scrip_code_and_event() -> None:
    payload = b'''<html><body>
      <a href="MAnnDet.aspx?flag=C&amp;newsid=ABC&amp;scrip_CD=500209&amp;type=A">
        Infosys Ltd -Announcement under Regulation 30 (LODR)-Investor Meet , Aug 30 2026 , 6:10PM
      </a>
    </body></html>'''

    records = parse_bse_disclosures(payload, "text/html", source_uri=BSE_CORPORATES_PAGE)

    assert len(records) == 1
    assert records[0].bse_code == "500209"
    assert records[0].company_name == "Infosys Ltd"
    assert records[0].published_at == datetime(2026, 8, 30, 12, 40, tzinfo=UTC)


def test_rbi_series_parser_uses_explicit_series_spec() -> None:
    payload = b"Date,Repo Rate\n29-08-2026,5.50\n30-08-2026,5.50\n"

    observations = parse_rbi_macro_series(
        payload,
        "text/csv",
        MacroSeriesSpec(
            series_key="repo_rate",
            unit="%",
            value_column="Repo Rate",
        ),
    )

    assert len(observations) == 2
    assert observations[-1].series_key == "repo_rate"
    assert Decimal(str(observations[-1].value)) == Decimal("5.50")


def test_nsdl_flow_parser_handles_parenthesized_negative_values() -> None:
    payload = (
        b"Date,FPI Net Equity (Rs. Cr),DII Net Equity (Rs. Cr)\n"
        b'30-08-2026,"(1,250.50)","2,100.25"\n'
    )
    observations = parse_nsdl_flows(payload, "text/csv")

    values = {item.series_key: Decimal(str(item.value)) for item in observations}
    assert values["fii_cash_net_cr"] == Decimal("-1250.50")
    assert values["dii_cash_net_cr"] == Decimal("2100.25")
