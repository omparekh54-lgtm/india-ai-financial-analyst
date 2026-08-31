from __future__ import annotations

from typing import Any

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.calculations.financials import growth_rate, margin

EARNINGS_SOURCE_TYPES = {
    "financial_fact",
    "exchange_filing",
    "company_filing",
    "earnings_release",
    "earnings_transcript",
    "earnings_presentation",
    "xbrl",
}


class EarningsManagementAgent:
    """Computes earnings deltas first; Agent 15 owns verification and final admission."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        earnings = agent_input.context.get("earnings") or {}
        if not isinstance(earnings, dict) or not earnings:
            return AgentOutput(
                agent=AgentName.EARNINGS,
                ok=False,
                warnings=["No earnings payload supplied"],
            )

        evidence = [
            item
            for item in agent_input.evidence
            if item.source_type in EARNINGS_SOURCE_TYPES
            or item.section in {"financial_results", "earnings_call", "earnings_transcript", "investor_presentation"}
        ]
        evidence_ids = [item.evidence_id for item in evidence]
        revenue = _number(earnings.get("revenue"))
        prior_revenue = _number(earnings.get("prior_revenue"))
        pat = _number(earnings.get("pat"))
        prior_pat = _number(earnings.get("prior_pat"))
        ebitda = _number(earnings.get("ebitda"))
        period = str(earnings.get("period") or "") or None

        metrics = {
            "revenue_growth": growth_rate(revenue, prior_revenue),
            "pat_growth": growth_rate(pat, prior_pat),
            "ebitda_margin": margin(ebitda, revenue),
            "guidance": earnings.get("guidance"),
            "period": period,
            "published_at": earnings.get("published_at"),
        }

        transcript = str(earnings.get("management_commentary") or "")
        language_flags = _management_language_flags(transcript)
        metrics["management_language_flags"] = language_flags

        claims: list[Claim] = []
        calculation_specs = (
            (
                "revenue_growth",
                metrics.get("revenue_growth"),
                {
                    "operation": "growth",
                    "current": revenue,
                    "previous": prior_revenue,
                },
                0.88,
            ),
            (
                "pat_growth",
                metrics.get("pat_growth"),
                {
                    "operation": "growth",
                    "current": pat,
                    "previous": prior_pat,
                },
                0.84,
            ),
            (
                "ebitda_margin",
                metrics.get("ebitda_margin"),
                {
                    "operation": "ratio",
                    "numerator": ebitda,
                    "denominator": revenue,
                    "scale": 1.0,
                },
                0.84,
            ),
        )
        for name, value, calculation, materiality in calculation_specs:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            claims.append(
                Claim(
                    agent=AgentName.EARNINGS,
                    statement=f"{name} calculated as {float(value):.4f}",
                    claim_type="calculation",
                    confidence=0.99 if evidence_ids else 0.45,
                    evidence_ids=evidence_ids,
                    status="pending",
                    metric=name,
                    value=float(value),
                    unit="ratio",
                    period=period,
                    materiality=materiality,
                    calculation_version=f"earnings.{name}.v2",
                    data={
                        "metric": name,
                        "value": float(value),
                        "period": period,
                        "calculation": calculation,
                    },
                )
            )

        guidance = earnings.get("guidance")
        if guidance:
            claims.append(
                Claim(
                    agent=AgentName.EARNINGS,
                    statement="Management guidance is available for the current earnings period",
                    claim_type="fact",
                    confidence=0.90 if evidence_ids else 0.40,
                    evidence_ids=evidence_ids,
                    status="pending",
                    period=period,
                    materiality=0.88,
                    data={"guidance": guidance, "period": period},
                )
            )

        warnings: list[str] = []
        if claims and not evidence_ids:
            warnings.append(
                "Earnings calculations are present but no earnings-specific source evidence is linked"
            )
        return AgentOutput(
            agent=AgentName.EARNINGS,
            claims=claims,
            evidence=evidence,
            metrics=metrics,
            warnings=warnings,
        )


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
