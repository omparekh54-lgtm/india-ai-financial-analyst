from __future__ import annotations

import pandas as pd

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.calculations.technicals import atr, macd, realized_volatility, rsi


class TechnicalDerivativesAgent:
    """Deterministic technical layer; derivatives fields are added with broker data."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        bars = agent_input.context.get("market_bars") or []
        if len(bars) < 30:
            return AgentOutput(
                agent=AgentName.TECHNICAL,
                ok=False,
                warnings=["At least 30 market bars are required for technical analysis"],
            )

        frame = pd.DataFrame(bars).sort_values("ts")
        required = {"high", "low", "close"}
        if not required.issubset(frame.columns):
            return AgentOutput(
                agent=AgentName.TECHNICAL,
                ok=False,
                errors=["market_bars must include high, low and close"],
            )

        close = pd.to_numeric(frame["close"], errors="coerce")
        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")

        macd_frame = macd(close)
        metrics = {
            "rsi_14": _last_valid(rsi(close)),
            "macd": _last_valid(macd_frame["macd"]),
            "macd_signal": _last_valid(macd_frame["signal"]),
            "atr_14": _last_valid(atr(high, low, close)),
            "realized_volatility_20d": _last_valid(realized_volatility(close)),
        }
        evidence = [item for item in agent_input.evidence if item.source_type == "market_data"]
        evidence_ids = [item.evidence_id for item in evidence]
        claims = [
            Claim(
                agent=AgentName.TECHNICAL,
                statement=f"{name} calculated as {value:.4f}",
                claim_type="calculation",
                confidence=0.99,
                evidence_ids=evidence_ids,
                status="pending",
                data={"metric": name, "value": value},
            )
            for name, value in metrics.items()
            if value is not None
        ]
        warnings = [] if evidence_ids else ["Technical calculations lack market-data provenance"]
        return AgentOutput(
            agent=AgentName.TECHNICAL,
            claims=claims,
            evidence=evidence,
            metrics=metrics,
            warnings=warnings,
        )


def _last_valid(series: pd.Series) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])
