from decimal import Decimal

from app.ingestion.financials import normalize_financial_facts
from app.ingestion.xbrl_financials import parse_financial_xbrl


def test_xml_xbrl_extracts_duration_and_instant_financial_facts() -> None:
    payload = b'''<?xml version="1.0" encoding="UTF-8"?>
    <xbrli:xbrl
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:ind="https://example.test/financial">
      <xbrli:context id="Q1">
        <xbrli:entity>
          <xbrli:identifier scheme="https://example.test">INFY</xbrli:identifier>
        </xbrli:entity>
        <xbrli:period>
          <xbrli:startDate>2026-04-01</xbrli:startDate>
          <xbrli:endDate>2026-06-30</xbrli:endDate>
        </xbrli:period>
      </xbrli:context>
      <xbrli:context id="I1">
        <xbrli:entity>
          <xbrli:identifier scheme="https://example.test">INFY</xbrli:identifier>
        </xbrli:entity>
        <xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period>
      </xbrli:context>
      <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
      <ind:RevenueFromOperations contextRef="Q1" unitRef="INR">1250000000</ind:RevenueFromOperations>
      <ind:ProfitAfterTax contextRef="Q1" unitRef="INR">250000000</ind:ProfitAfterTax>
      <ind:CashAndCashEquivalents contextRef="I1" unitRef="INR">800000000</ind:CashAndCashEquivalents>
    </xbrli:xbrl>'''

    facts = normalize_financial_facts(parse_financial_xbrl(payload, "application/xml"))
    by_name = {fact.fact_name: fact for fact in facts}

    assert by_name["revenue"].value == Decimal(1250000000)
    assert by_name["revenue"].period_type == "quarterly"
    assert by_name["pat"].value == Decimal(250000000)
    assert by_name["cash"].value == Decimal(800000000)
    assert by_name["cash"].period_type == "point_in_time"


def test_xml_xbrl_applies_scale_before_financial_normalization() -> None:
    payload = b'''<?xml version="1.0"?>
    <xbrli:xbrl
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:ind="https://example.test/financial">
      <xbrli:context id="FY">
        <xbrli:entity><xbrli:identifier scheme="test">TCS</xbrli:identifier></xbrli:entity>
        <xbrli:period>
          <xbrli:startDate>2025-04-01</xbrli:startDate>
          <xbrli:endDate>2026-03-31</xbrli:endDate>
        </xbrli:period>
      </xbrli:context>
      <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
      <ind:RevenueFromOperations contextRef="FY" unitRef="INR" scale="3">1250</ind:RevenueFromOperations>
    </xbrli:xbrl>'''

    facts = normalize_financial_facts(parse_financial_xbrl(payload, "application/xbrl+xml"))

    assert facts[0].fact_name == "revenue"
    assert facts[0].value == Decimal(1250000)
    assert facts[0].period_type == "annual"


def test_inline_xbrl_extracts_nonfraction_fact() -> None:
    payload = b'''<!doctype html>
    <html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
          xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
      <body>
        <div style="display:none">
          <xbrli:context id="Q1">
            <xbrli:entity><xbrli:identifier scheme="test">HDFCBANK</xbrli:identifier></xbrli:entity>
            <xbrli:period>
              <xbrli:startDate>2026-04-01</xbrli:startDate>
              <xbrli:endDate>2026-06-30</xbrli:endDate>
            </xbrli:period>
          </xbrli:context>
          <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
        </div>
        <ix:nonFraction name="ind:ProfitAfterTax" contextRef="Q1" unitRef="INR">1,250</ix:nonFraction>
      </body>
    </html>'''

    facts = normalize_financial_facts(parse_financial_xbrl(payload, "text/html"))

    assert len(facts) == 1
    assert facts[0].fact_name == "pat"
    assert facts[0].value == Decimal(1250)
    assert facts[0].period_type == "quarterly"
    assert facts[0].metadata["source_format"] == "ixbrl"
