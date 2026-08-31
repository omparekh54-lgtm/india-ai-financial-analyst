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


@pytest.mark.asyncio
async def test_bank_uses_real_peer_median_pb_when_target_not_supplied() -> None:
    output = await ValuationScenarioAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="Analyze peer-valued bank",
            context={
                "valuation_inputs": {
                    "sector": "Financial Services",
                    "industry": "Banks",
                    "book_value_per_share": 100.0,
                    "current_price": 175.0,
                },
                "peers": [
                    {"legal_name": "Peer A", "pb": 1.8},
                    {"legal_name": "Peer B", "pb": 2.2},
                    {"legal_name": "Peer C", "pb": 2.0},
                ],
            },
        )
    )

    assert output.ok is True
    assert output.metrics["method"] == "price_to_book"
    assert output.metrics["scenarios"]["base"] == pytest.approx(200.0)
    assert output.metrics["input_provenance"]["target_pb_source"] == "peer_median"


@pytest.mark.asyncio
async def test_general_company_falls_back_from_missing_dcf_to_peer_pe() -> None:
    output = await ValuationScenarioAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="Analyze IT company",
            context={
                "valuation_inputs": {
                    "sector": "Information Technology",
                    "industry": "IT Services",
                    "current_price": 450.0,
                },
                "financials": {"eps": 20.0},
                "peers": [
                    {"legal_name": "Peer A", "pe": 24.0},
                    {"legal_name": "Peer B", "pe": 26.0},
                ],
            },
        )
    )

    assert output.ok is True
    assert output.metrics["method"] == "price_to_earnings"
    assert output.metrics["scenarios"]["base"] == pytest.approx(500.0)
    unavailable = output.metrics["unavailable_methods"]
    assert any(item["method"] == "dcf" for item in unavailable)


@pytest.mark.asyncio
async def test_metals_route_uses_peer_ev_ebitda_without_inventing_multiple() -> None:
    output = await ValuationScenarioAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="Analyze steel producer",
            context={
                "valuation_inputs": {
                    "sector": "Metals & Mining",
                    "industry": "Steel",
                    "current_price": 100.0,
                    "net_debt": 200.0,
                    "shares_outstanding": 10.0,
                },
                "financials": {"ebitda": 100.0},
                "peers": [
                    {"legal_name": "Peer A", "ev_ebitda": 5.0},
                    {"legal_name": "Peer B", "ev_ebitda": 7.0},
                ],
            },
        )
    )

    assert output.ok is True
    assert output.metrics["method"] == "ev_to_ebitda"
    assert output.metrics["scenarios"]["base"] == pytest.approx(40.0)
    assert output.metrics["input_provenance"]["target_ev_ebitda_source"] == "peer_median"


@pytest.mark.asyncio
async def test_agent_refuses_to_invent_missing_valuation_assumptions() -> None:
    output = await ValuationScenarioAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="Analyze company with incomplete valuation inputs",
            context={
                "valuation_inputs": {
                    "sector": "Information Technology",
                    "industry": "IT Services",
                    "current_price": 450.0,
                }
            },
        )
    )

    assert output.ok is False
    assert output.metrics["sector_family"] == "general_corporate"
    assert output.warnings
    assert "did not invent missing assumptions" in output.warnings[0]
