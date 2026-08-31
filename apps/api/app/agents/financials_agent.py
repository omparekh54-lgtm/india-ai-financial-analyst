from __future__ import annotations

from typing import Any

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

FINANCIAL_SOURCE_TYPES = {
    "financial_fact",
    "exchange_filing",
    "company_filing",
    "regulator",
}

SECTOR_KPIS = (
    "net_interest_income",
    "interest_income",
    "deposits",
    "advances",
    "gross_advances",
    "net_advances",
    "provisions",
    "provision_coverage_pct",
    "gross_npa_pct",
    "net_npa_pct",
    "nim_pct",
    "casa_ratio_pct",
    "credit_cost_pct",
    "capital_adequacy_pct",
    "roa_pct",
    "roe_pct",
    "gross_stage3_pct",
    "net_stage3_pct",
    "aum",
    "disbursements",
    "gross_written_premium",
    "new_business_premium",
    "ape",
    "vnb",
    "vnb_margin_pct",
    "embedded_value",
    "embedded_value_per_share",
    "solvency_ratio_pct",
    "combined_ratio_pct",
    "expense_ratio_pct",
    "claims_incurred",
    "persistency_ratio_pct",
    "attrition_pct",
    "utilization_pct",
    "tcv",
    "constant_currency_growth_pct",
    "employee_count",
    "volume_growth_pct",
    "market_share_pct",
    "order_book",
    "capacity_utilization_pct",
)


class FinancialForensicAgent:
    """Code-first financial analysis agent over normalized sourced facts."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        facts = agent_input.context.get("financials") or {}
        if not facts:
            return AgentOutput(
                agent=AgentName.FINANCIALS,
                ok=False,
                warnings=["No normalized financial facts were supplied"],
            )

        calculated_metrics = {
            "revenue_growth": growth_rate(facts.get("revenue"), facts.get("previous_revenue")),
            "ebitda_margin": margin(facts.get("ebitda"), facts.get("revenue")),
            "net_margin": margin(facts.get("pat"), facts.get("revenue")),
            "cfo_to_pat": cfo_to_pat(facts.get("cfo"), facts.get("pat")),
            "net_debt_to_ebitda": net_debt_to_ebitda(
                facts.get("total_debt"), facts.get("cash"), facts.get("ebitda")
            ),
            "interest_coverage": interest_coverage(
                facts.get("ebit"), facts.get("interest_expense")
            ),
            "roce": roce(
                facts.get("ebit"), facts.get("total_assets"), facts.get("current_liabilities")
            ),
        }
        calculated_metrics.update(
            working_capital_days(
                facts.get("receivables"),
                facts.get("inventory"),
                facts.get("payables"),
                facts.get("revenue"),
                facts.get("cogs"),
            )
        )

        sector_metrics = {
            name: value
            for name in SECTOR_KPIS
            if (value := _number(facts.get(name))) is not None
        }
        evidence = [item for item in agent_input.evidence if item.source_type in FINANCIAL_SOURCE_TYPES]
        evidence_ids = [item.evidence_id for item in evidence]

        claims = [
            Claim(
                agent=AgentName.FINANCIALS,
                statement=f"{name} calculated as {value:.4f}",
                claim_type="calculation",
                confidence=0.99,
                evidence_ids=evidence_ids,
                status="pending",
                data={"metric": name, "value": value},
            )
            for name, value in calculated_metrics.items()
            if value is not None
        ]
        claims.extend(
            Claim(
                agent=AgentName.FINANCIALS,
                statement=f"{name} reported at {value:.4f}",
                claim_type="fact",
                confidence=0.98,
                evidence_ids=evidence_ids,
                status="pending",
                data={
                    "metric": name,
                    "value": value,
                    "period_end": facts.get(f"{name}_period_end"),
                    "sector_kpi": True,
                },
            )
            for name, value in sector_metrics.items()
        )

        warnings = [] if evidence_ids else ["Financial calculations lack source-linked evidence"]
        metrics: dict[str, object] = {
            **calculated_metrics,
            "sector_kpis": sector_metrics,
        }
        return AgentOutput(
            agent=AgentName.FINANCIALS,
            claims=claims,
            evidence=evidence,
            metrics=metrics,
            warnings=warnings,
        )


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
