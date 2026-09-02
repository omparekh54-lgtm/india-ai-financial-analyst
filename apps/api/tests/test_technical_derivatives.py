from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.agents.contracts import AgentInput, EvidenceRef
from app.agents.technical_agent import TechnicalDerivativesAgent


def _bars() -> list[dict[str, object]]:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    return [
        {
            "ts": (start + timedelta(days=index)).isoformat(),
            "high": 100.0 + index * 0.5,
            "low": 98.0 + index * 0.5,
            "close": 99.0 + index * 0.5,
        }
        for index in range(40)
    ]


@pytest.mark.asyncio
async def test_technical_agent_calculates_optional_derivatives_context() -> None:
    derivative_evidence = EvidenceRef(
        source_type="derivatives_data",
        source_uri="broker://option-chain/example",
        title="Normalized derivatives snapshot",
        retrieved_at=datetime.now(UTC).isoformat(),
        freshness="near_live",
        source_priority=2,
    )
    output = await TechnicalDerivativesAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            context={
                "market_bars": _bars(),
                "derivatives": {
                    "spot_price": 100.0,
                    "futures": {
                        "price": 102.0,
                        "open_interest": 120.0,
                        "previous_open_interest": 100.0,
                        "rollover_pct": 71.5,
                    },
                    "options": [
                        {"strike": 90, "option_type": "call", "open_interest": 100},
                        {"strike": 90, "option_type": "put", "open_interest": 20},
                        {
                            "strike": 100,
                            "option_type": "call",
                            "open_interest": 200,
                            "implied_volatility": 0.25,
                            "delta": 0.5,
                            "gamma": 0.02,
                        },
                        {
                            "strike": 100,
                            "option_type": "put",
                            "open_interest": 300,
                            "implied_volatility": 0.27,
                            "delta": -0.5,
                            "gamma": 0.02,
                        },
                        {"strike": 110, "option_type": "call", "open_interest": 50},
                        {"strike": 110, "option_type": "put", "open_interest": 100},
                    ],
                },
            },
            evidence=[derivative_evidence],
        )
    )

    derivatives = output.metrics["derivatives"]
    assert derivatives["futures_basis_pct"] == pytest.approx(2.0)
    assert derivatives["futures_oi_change_pct"] == pytest.approx(20.0)
    assert derivatives["rollover_pct"] == pytest.approx(71.5)
    assert derivatives["put_call_oi_ratio"] == pytest.approx(1.2)
    assert derivatives["atm_implied_volatility"] == pytest.approx(0.26)
    assert derivatives["max_pain_strike"] == pytest.approx(100.0)
    assert derivatives["max_pain_distance_pct"] == pytest.approx(0.0)
    assert derivatives["atm_call_delta"] == pytest.approx(0.5)
    assert derivatives["atm_put_delta"] == pytest.approx(-0.5)
    assert any(claim.data.get("category") == "derivatives" for claim in output.claims)


@pytest.mark.asyncio
async def test_technical_agent_does_not_fake_derivatives_when_context_is_absent() -> None:
    output = await TechnicalDerivativesAgent().run(
        AgentInput(
            job_id=uuid4(),
            query="EXAMPLE",
            context={"market_bars": _bars()},
        )
    )

    assert "derivatives" not in output.metrics
