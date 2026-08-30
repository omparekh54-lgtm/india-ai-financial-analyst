from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.financials import normalize_financial_facts
from app.ingestion.reference_files import ReferenceFileError
from app.ingestion.reference_financials import parse_financial_csv


def test_financial_csv_parses_and_derives_canonical_facts() -> None:
    content = """fact_name,period_end,period_type,value,unit,period_start,metadata_json
Cash Flow From Operating Activities,2026-03-31,FY,1250,INR cr,2025-04-01,"{""audited"":true}"
Capital Expenditure,2026-03-31,annual,300,INR cr,2025-04-01,
"""
    facts = parse_financial_csv(content, min_rows=2)
    normalized = normalize_financial_facts(facts)
    by_name = {fact.fact_name: fact for fact in normalized}

    assert len(facts) == 2
    assert by_name["cfo"].period_end == date(2026, 3, 31)
    assert by_name["cfo"].metadata["audited"] is True
    assert by_name["free_cash_flow"].value == 950


def test_financial_csv_rejects_alias_duplicates() -> None:
    content = """fact_name,period_end,period_type,value,unit
PAT,2026-03-31,FY,100,INR cr
Profit After Tax,2026-03-31,annual,100,INR cr
"""
    with pytest.raises(ReferenceFileError, match="duplicate canonical financial fact"):
        parse_financial_csv(content)


def test_financial_csv_rejects_non_object_metadata() -> None:
    content = """fact_name,period_end,period_type,value,metadata_json
Revenue,2026-03-31,FY,1000,"[1,2]"
"""
    with pytest.raises(ReferenceFileError, match="must be a JSON object"):
        parse_financial_csv(content)


def test_financial_csv_rejects_missing_required_fields() -> None:
    content = """fact_name,period_end,period_type,value
Revenue,2026-03-31,FY,
"""
    with pytest.raises(ReferenceFileError, match="are required"):
        parse_financial_csv(content)


def test_financial_csv_enforces_minimum_rows() -> None:
    content = """fact_name,period_end,period_type,value
Revenue,2026-03-31,FY,1000
"""
    with pytest.raises(ReferenceFileError, match="minimum expected is 2"):
        parse_financial_csv(content, min_rows=2)
