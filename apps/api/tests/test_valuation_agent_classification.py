from __future__ import annotations

from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput
from app.agents.valuation_agent import ValuationScenarioAgent


@pytest.mark.asyncio
async def test_insurance_industry_overrides_generic_financial_services_sector() -> None:
    output = await ValuationScenarioAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="Analyze insurer",
            context={
                "valuation_inputs": {
                    "sector": "Financial Services",
                    "industry": "Insurance",
                    "embedded_value_per_share": 100.0,
                    "target_pev": 2.0,
                    "current_price": 175.0,
                }
            },
        )
    )

    assert output.ok is True
    assert output.metrics["method"] == "price_to_embedded_value"
    assert output.metrics["scenarios"]["base"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_bank_industry_routes_generic_financial_services_to_price_to_book() -> None:
    output = await ValuationScenarioAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="Analyze bank",
            context={
                "valuation_inputs": {
                    "sector": "Financial Services",
                    "industry": "Banks",
                    "book_value_per_share": 100.0,
                    "target_pb": 2.0,
                    "current_price": 175.0,
                }
            },
        )
    )

    assert output.ok is True
    assert output.metrics["method"] == "price_to_book"
    assert output.metrics["scenarios"]["base"] == pytest.approx(200.0)
