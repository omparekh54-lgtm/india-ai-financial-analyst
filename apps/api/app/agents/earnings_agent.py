from __future__ import annotations

from typing import Any

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.calculations.financials import growth_rate, margin


class EarningsManagementAgent:
    """Extracts deterministic earnings deltas before narrative interpretation."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        earnings = agent_input.context.get("earnings") or {}
        if not earnings:
            return AgentOutput(
                agent=AgentName.EARNINGS,
                ok=False,
                warnings=["No earnings payload supplied"],
            )

        revenue = _number(earnings.get("revenue"))
        prior_revenue = _number(earnings.get("prior_revenue"))
        pat = _number(earnings.get("pat"))
        prior_pat = _number(earnings.get("prior_pat"))
        ebitda = _number(earnings.get("ebitda"))

        metrics = {
            "revenue_growth": growth_rate(revenue, prior_revenue),
            "pat_growth": growth_rate(pat, prior_pat),
            "ebitda_margin": margin(ebitda, revenue),
            "guidance": earnings.get("guidance"),
            "period": earnings.get("period"),
            "published_at": earnings.get("published_at"),
        }

        transcript = str(earnings.get("management_commentary") or "")
        language_flags = _management_language_flags(transcript)
        metrics["management_language_flags"] = language_flags

        claims: list[Claim] = []
        for name in ("revenue_growth", "pat_growth", "ebitda_margin"):
            value = metrics.get(name)
            if isinstance(value, (int, float)):
                claims.append(
                    Claim(
                        agent=AgentName.EARNINGS,
                        statement=f"{name} calculated as {value:.4f}",
                        claim_type="calculation",
                        confidence=1.0,
                        status="verified",
                        data={"metric": name, "value": value, "period": metrics["period"]},
                    )
                )

        guidance = earnings.get("guidance")
        if guidance:
            claims.append(
                Claim(
                    agent=AgentName.EARNINGS,
                    statement="Management guidance is available for the current earnings period",
                    claim_type="fact",
                    confidence=0.95,
                    status="supported",
                    data={"guidance": guidance, "period": metrics["period"]},
                )
            )

        return AgentOutput(agent=AgentName.EARNINGS, claims=claims, metrics=metrics)


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _management_language_flags(text: str) -> list[str]:
    lowered = text.lower()
    flags: list[str] = []
    patterns = {
        "demand_caution": ("soft demand", "demand weakness", "uncertain demand", "slower demand"),
        "margin_pressure": ("margin pressure", "cost pressure", "input cost", "pricing pressure"),
        "growth_confidence": ("strong pipeline", "robust demand", "confident of growth", "healthy growth"),
        "working_capital": ("working capital", "receivable", "inventory build"),
    }
    for label, phrases in patterns.items():
        if any(phrase in lowered for phrase in phrases):
            flags.append(label)
    return flags
