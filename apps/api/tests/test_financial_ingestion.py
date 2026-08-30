from datetime import date
from decimal import Decimal

from app.ingestion.financials import (
    RawFinancialFact,
    canonical_fact_name,
    normalize_financial_facts,
)


def test_financial_aliases_include_india_sector_metrics() -> None:
    assert canonical_fact_name("Revenue from Operations") == "revenue"
    assert canonical_fact_name("Gross NPA %") == "gross_npa_pct"
    assert canonical_fact_name("VNB Margin") == "vnb_margin_pct"


def test_financial_normalization_derives_free_cash_flow() -> None:
    period_end = date(2026, 3, 31)
    facts = normalize_financial_facts(
        [
            RawFinancialFact(
                name="Cash Flow From Operating Activities",
                period_end=period_end,
                period_type="FY",
                value="1,250",
                unit="INR cr",
            ),
            RawFinancialFact(
                name="Capital Expenditure",
                period_end=period_end,
                period_type="annual",
                value=Decimal("300"),
                unit="INR cr",
            ),
        ]
    )

    by_name = {fact.fact_name: fact for fact in facts}
    assert by_name["free_cash_flow"].value == Decimal("950")
    assert by_name["free_cash_flow"].metadata["derived"] is True
    assert by_name["cfo"].period_type == "annual"
