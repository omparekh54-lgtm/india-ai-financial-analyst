from datetime import date

import pytest

from app.connectors.rbi_bulletin import (
    parse_latest_select_economic_indicators_url,
    parse_rbi_bulletin_ten_year_yield,
)


def test_parse_latest_select_economic_indicators_url() -> None:
    html = """
    <html><body>
      <a href="/Scripts/BS_ViewBulletin.aspx?Id=24306">1. Select Economic Indicators</a>
      <a href="/Scripts/BS_ViewBulletin.aspx?Id=24307">2. RBI – Liabilities and Assets</a>
    </body></html>
    """

    assert parse_latest_select_economic_indicators_url(html) == (
        "https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24306"
    )


def test_parse_latest_select_economic_indicators_url_rejects_missing_or_external() -> None:
    with pytest.raises(ValueError, match="did not expose"):
        parse_latest_select_economic_indicators_url("<html><body>No table</body></html>")

    with pytest.raises(ValueError, match="official RBI HTTPS host"):
        parse_latest_select_economic_indicators_url(
            '<a href="https://example.com/BS_ViewBulletin.aspx?Id=1">Select Economic Indicators</a>'
        )


def test_parse_rbi_bulletin_ten_year_yield_uses_latest_month_end() -> None:
    html = """
    <html><body>
      <div>Date : Jul 22, 2026</div>
      <table>
        <tr><th></th><th>2025-26</th><th>2025</th><th></th><th>2026</th><th></th></tr>
        <tr><th>Item</th><th></th><th>Apr.</th><th>May</th><th>Apr.</th><th>May</th></tr>
        <tr>
          <td>4.14 10-Year G-Sec Par Yield (FBIL)</td>
          <td>7.11</td><td>6.40</td><td>6.23</td><td>7.08</td><td>6.99</td>
        </tr>
      </table>
    </body></html>
    """

    observation = parse_rbi_bulletin_ten_year_yield(
        html,
        source_uri="https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24306",
    )

    assert observation.series_key == "india_10y_yield"
    assert observation.observation_date == date(2026, 5, 31)
    assert observation.value == 6.99
    assert observation.unit == "percent"
    assert observation.metadata["publication_date"] == "2026-07-22"


def test_parse_rbi_bulletin_ten_year_yield_handles_december_year_rollover() -> None:
    html = """
    <html><body>
      <div>Date : Jan 20, 2026</div>
      <table>
        <tr><th></th><th>2025</th></tr>
        <tr><th>Item</th><th>Dec.</th></tr>
        <tr><td>10-Year G-Sec Par Yield (FBIL)</td><td>6.67</td></tr>
      </table>
    </body></html>
    """

    observation = parse_rbi_bulletin_ten_year_yield(
        html,
        source_uri="https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24000",
    )

    assert observation.observation_date == date(2025, 12, 31)
    assert observation.value == 6.67


def test_parse_rbi_bulletin_ten_year_yield_rejects_missing_series() -> None:
    html = """
    <html><body><div>Date : Jul 22, 2026</div><table>
      <tr><th>Item</th><th>May</th></tr>
      <tr><td>Policy Repo Rate</td><td>5.25</td></tr>
    </table></body></html>
    """

    with pytest.raises(ValueError, match="does not contain"):
        parse_rbi_bulletin_ten_year_yield(
            html,
            source_uri="https://www.rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24306",
        )
