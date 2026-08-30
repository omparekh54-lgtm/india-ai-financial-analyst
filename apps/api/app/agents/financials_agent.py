from __future__ import annotations

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.calculations.financials import (
    cfo_to_pat,
    growth_rate,
    interest_coverage,
    margin,
    net_debt_to_ebitda,
    roce,
    working_capital_days,
)


class FinancialForensicAgent:
    """Code-first financial analysis agent.

    The ingestion layer supplies normalized financial facts in ``context['financials']``.
    This agent performs deterministic calculations before any LLM interpretation.
    """

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        facts = agent_input.context.get("financials") or {}
        if not facts:
            return AgentOutput(
                agent=AgentName.FINANCIALS,
                ok=False,
                warnings=["No normalized financial facts were supplied"],
            )

        metrics = {
            "revenue_growth": growth_rate(facts.get("revenue"), facts.get("previous_revenue")),
            "ebitda_margin": margin(facts.get("ebitda"), facts.get("revenue")),
            "net_margin": margin(facts.get("pat"), facts.get("revenue")),
            "cfo_to_pat": cfo_to_pat(facts.get("cfo"), facts.get("pat")),
            "net_debt_to_ebitda": net_debt_to_ebitda(
                facts.get("total_debt"),
                facts.get("cash"),
                facts.get("ebitda"),
            ),
            "interest_coverage": interest_coverage(
                facts.get("ebit"),
                facts.get("interest_expense"),
            ),
            "roce": roce(
                facts.get("ebit"),
                facts.get("total_assets"),
                facts.get("current_liabilities"),
            ),
        }
        metrics.update(
            working_capital_days(
                facts.get("receivables"),
                facts.get("inventory"),
                facts.get("payables"),
                facts.get("revenue"),
                facts.get("cogs"),
            )
        )

        claims = [
            Claim(
                agent=AgentName.FINANCIALS,
                statement=f"{name} calculated as {value:.4f}",
                claim_type="calculation",
                confidence=1.0,
                status="verified",
                data={"metric": name, "value": value},
            )
            for name, value in metrics.items()
            if value is not None
        ]

        return AgentOutput(
            agent=AgentName.FINANCIALS,
            claims=claims,
            metrics=metrics,
        )
