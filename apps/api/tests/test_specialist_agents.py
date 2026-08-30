from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput
from app.agents.earnings_agent import EarningsManagementAgent
from app.agents.industry_agent import IndustryPeerAgent
from app.agents.macro_agent import IndiaMacroPolicyFlowAgent
from app.agents.market_agent import LiveMarketAgent
from app.agents.news_agent import NewsEventAgent
from app.agents.sentiment_agent import SentimentNarrativeAgent
from app.agents.valuation_agent import ValuationScenarioAgent


def _input(context: dict[str, object]) -> AgentInput:
    return AgentInput(job_id=uuid4(), query="RELIANCE", context=context)


@pytest.mark.asyncio
async def test_market_agent_calculates_relative_move() -> None:
    result = await LiveMarketAgent().run(
        _input(
            {
                "market_quote": {
                    "price": 110,
                    "previous_close": 100,
                    "volume": 200,
                    "average_volume": 100,
                    "provider": "fixture",
                    "is_delayed": False,
                },
                "benchmark": {"name": "NIFTY 50", "change_pct": 5},
                "sector_benchmark": {"name": "NIFTY ENERGY", "change_pct": 7},
            }
        )
    )
    assert result.metrics["change_pct"] == pytest.approx(10)
    assert result.metrics["relative_to_benchmark_pct"] == pytest.approx(5)
    assert result.metrics["volume_ratio"] == pytest.approx(2)


@pytest.mark.asyncio
async def test_earnings_agent_calculates_growth_and_margin() -> None:
    result = await EarningsManagementAgent().run(
        _input(
            {
                "earnings": {
                    "revenue": 120,
                    "prior_revenue": 100,
                    "pat": 22,
                    "prior_pat": 20,
                    "ebitda": 30,
                    "period": "Q1 FY27",
                    "management_commentary": "Robust demand and a strong pipeline remain visible.",
                }
            }
        )
    )
    assert result.metrics["revenue_growth"] == pytest.approx(0.2)
    assert result.metrics["ebitda_margin"] == pytest.approx(0.25)
    assert "growth_confidence" in result.metrics["management_language_flags"]


@pytest.mark.asyncio
async def test_news_agent_deduplicates_and_scores_materiality() -> None:
    result = await NewsEventAgent().run(
        _input(
            {
                "news_events": [
                    {"title": "Company announces Q1 earnings", "url": "https://example.com/1"},
                    {"title": "Company announces Q1 earnings!", "url": "https://example.com/2"},
                    {"title": "Company opens a new office", "url": "https://example.com/3"},
                ]
            }
        )
    )
    assert result.metrics["event_count"] == 2
    assert result.metrics["high_materiality_count"] == 1


@pytest.mark.asyncio
async def test_industry_agent_uses_peer_medians() -> None:
    result = await IndustryPeerAgent().run(
        _input(
            {
                "company_metrics": {"pe": 25, "revenue_growth": 0.15},
                "peers": [
                    {"name": "A", "pe": 20, "revenue_growth": 0.10},
                    {"name": "B", "pe": 22, "revenue_growth": 0.12},
                    {"name": "C", "pe": 24, "revenue_growth": 0.08},
                ],
            }
        )
    )
    assert result.metrics["peer_medians"]["pe"] == pytest.approx(22)
    assert result.metrics["relative_to_peer_median"]["pe"] == pytest.approx(3)


@pytest.mark.asyncio
async def test_macro_agent_surfaces_india_flow_and_fx_flags() -> None:
    result = await IndiaMacroPolicyFlowAgent().run(
        _input(
            {
                "macro": {
                    "usd_inr_change_pct": 1.2,
                    "brent_change_pct": 4.0,
                    "fii_cash_net_cr": -2500,
                },
                "macro_exposure": {"fx": "net importer", "crude": "input cost sensitive"},
            }
        )
    )
    assert len(result.metrics["material_macro_flags"]) == 3
    assert len(result.claims) == 3


@pytest.mark.asyncio
async def test_valuation_agent_routes_bank_to_price_to_book() -> None:
    result = await ValuationScenarioAgent().run(
        _input(
            {
                "valuation_inputs": {
                    "sector": "Banking",
                    "book_value_per_share": 100,
                    "target_pb": 2,
                    "current_price": 180,
                }
            }
        )
    )
    assert result.metrics["method"] == "price_to_book"
    assert result.metrics["scenarios"]["base"] == pytest.approx(200)


@pytest.mark.asyncio
async def test_sentiment_agent_has_transparent_lexicon_score() -> None:
    result = await SentimentNarrativeAgent().run(
        _input({"narratives": ["Strong growth, robust demand and improved margin."]})
    )
    assert result.metrics["sentiment_label"] == "positive"
    assert result.metrics["sentiment_score"] > 0
