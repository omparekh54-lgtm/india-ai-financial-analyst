from __future__ import annotations

from typing import Any
from uuid import UUID

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.calculations.financials import (
    altman_z_score,
    beneish_m_score,
    cfo_to_pat,
    growth_rate,
    interest_coverage,
    margin,
    net_debt_to_ebitda,
    piotroski_f_score,
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
    "loan_growth_pct",
    "deposit_growth_pct",
    "credit_deposit_ratio_pct",
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
    "aum_growth_pct",
    "disbursements",
    "borrowing_cost_pct",
    "alm_gap_pct",
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
    "deal_wins_value",
    "constant_currency_growth_pct",
    "employee_count",
    "volume_growth_pct",
    "price_growth_pct",
    "rural_growth_pct",
    "urban_growth_pct",
    "distribution_outlets",
    "input_cost_inflation_pct",
    "vehicle_volume",
    "asp",
    "market_share_pct",
    "exports",
    "ev_penetration_pct",
    "inventory_days",
    "order_book",
    "capacity_utilization_pct",
    "realization_per_unit",
    "production_volume",
    "sales_volume",
    "spread_per_unit",
    "normalized_ebitda",
)

FINANCIAL_SECTOR_MARKERS = (
    "bank",
    "banking",
    "financial services",
    "finance",
    "nbfc",
    "insurance",
    "asset management",
    "brokerage",
)

CALCULATION_INPUTS: dict[str, tuple[str, ...]] = {
    "revenue_growth": ("revenue", "previous_revenue"),
    "ebitda_margin": ("ebitda", "revenue"),
    "net_margin": ("pat", "revenue"),
    "cfo_to_pat": ("cfo", "pat"),
    "net_debt_to_ebitda": ("total_debt", "cash", "ebitda"),
    "interest_coverage": ("ebit", "interest_expense"),
    "roce": ("ebit", "total_assets", "current_liabilities"),
    "receivable_days": ("receivables", "revenue"),
    "inventory_days": ("inventory", "cogs"),
    "payable_days": ("payables", "cogs"),
    "cash_conversion_cycle": ("receivables", "inventory", "payables", "revenue", "cogs"),
}


class FinancialForensicAgent:
    """Code-first financial analysis agent over normalized sourced facts."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        facts = agent_input.context.get("financials") or {}
        if not isinstance(facts, dict) or not facts:
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
        period = _period(facts)
        fact_ids = _metadata_map(facts, "_fact_ids")
        fact_units = _metadata_map(facts, "_fact_units")

        claims = [
            Claim(
                agent=AgentName.FINANCIALS,
                statement=f"{name} calculated as {value:.4f}",
                claim_type="calculation",
                confidence=0.99,
                evidence_ids=evidence_ids,
                status="pending",
                metric=name,
                value=float(value),
                unit=_calculation_unit(name),
                period=period,
                calculation_version=f"financial.{name}.v1",
                input_metric_ids=_input_ids(fact_ids, CALCULATION_INPUTS.get(name, ())),
                data={
                    "metric": name,
                    "value": value,
                    "calculation": _calculation_payload(name, facts, calculated_metrics),
                },
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
                metric=name,
                value=float(value),
                unit=fact_units.get(name),
                period=str(facts.get(f"{name}_period_end") or period or "") or None,
                input_metric_ids=_input_ids(fact_ids, (name,)),
                data={
                    "metric": name,
                    "value": value,
                    "period_end": facts.get(f"{name}_period_end"),
                    "sector_kpi": True,
                },
            )
            for name, value in sector_metrics.items()
        )

        forensic_scores: dict[str, float | int] = {}
        if _forensic_scores_applicable(agent_input.context):
            piotroski = piotroski_f_score(facts)
            altman = altman_z_score(facts)
            beneish = beneish_m_score(facts)
            if piotroski is not None:
                forensic_scores["piotroski_f_score"] = piotroski
            if altman is not None:
                forensic_scores["altman_z_score"] = altman
            if beneish is not None:
                forensic_scores["beneish_m_score"] = beneish

        forensic_input_names = tuple(
            key for key in fact_ids if not key.startswith("_")
        )
        forensic_input_ids = _input_ids(fact_ids, forensic_input_names)
        for name, value in forensic_scores.items():
            claims.append(
                Claim(
                    agent=AgentName.FINANCIALS,
                    statement=f"{name} calculated as {float(value):.4f}",
                    claim_type="calculation",
                    confidence=0.98,
                    evidence_ids=evidence_ids,
                    status="pending",
                    metric=name,
                    value=float(value),
                    unit="score",
                    period=period,
                    materiality=0.70,
                    calculation_version=f"forensic.{name}.v1",
                    input_metric_ids=forensic_input_ids,
                    data={
                        "metric": name,
                        "value": value,
                        "forensic_score": True,
                        "applicability_checked": True,
                    },
                )
            )

        warnings = [] if evidence_ids else ["Financial calculations lack source-linked evidence"]
        if not _forensic_scores_applicable(agent_input.context):
            warnings.append(
                "Classic Piotroski/Altman/Beneish scores suppressed for a financial-sector issuer"
            )
        metrics: dict[str, object] = {
            **calculated_metrics,
            "sector_kpis": sector_metrics,
            "forensic_scores": forensic_scores,
        }
        return AgentOutput(
            agent=AgentName.FINANCIALS,
            claims=claims,
            evidence=evidence,
            metrics=metrics,
            warnings=warnings,
        )


def _calculation_payload(
    name: str,
    facts: dict[str, Any],
    calculated_metrics: dict[str, float | None],
) -> dict[str, object]:
    if name == "revenue_growth":
        return {"operation": "growth", "current": facts.get("revenue"), "previous": facts.get("previous_revenue")}
    if name == "ebitda_margin":
        return {"operation": "ratio", "numerator": facts.get("ebitda"), "denominator": facts.get("revenue")}
    if name == "net_margin":
        return {"operation": "ratio", "numerator": facts.get("pat"), "denominator": facts.get("revenue")}
    if name == "cfo_to_pat":
        return {"operation": "ratio", "numerator": facts.get("cfo"), "denominator": facts.get("pat")}
    if name == "net_debt_to_ebitda":
        return {
            "operation": "net_debt_to_ebitda",
            "total_debt": facts.get("total_debt"),
            "cash": facts.get("cash"),
            "ebitda": facts.get("ebitda"),
        }
    if name == "interest_coverage":
        return {"operation": "ratio", "numerator": facts.get("ebit"), "denominator": facts.get("interest_expense")}
    if name == "roce":
        return {
            "operation": "roce",
            "ebit": facts.get("ebit"),
            "total_assets": facts.get("total_assets"),
            "current_liabilities": facts.get("current_liabilities"),
        }
    if name == "receivable_days":
        return {"operation": "ratio", "numerator": facts.get("receivables"), "denominator": facts.get("revenue"), "scale": 365.0}
    if name == "inventory_days":
        return {"operation": "ratio", "numerator": facts.get("inventory"), "denominator": facts.get("cogs"), "scale": 365.0}
    if name == "payable_days":
        return {"operation": "ratio", "numerator": facts.get("payables"), "denominator": facts.get("cogs"), "scale": 365.0}
    if name == "cash_conversion_cycle":
        return {
            "operation": "cash_conversion_cycle",
            "receivable_days": calculated_metrics.get("receivable_days"),
            "inventory_days": calculated_metrics.get("inventory_days"),
            "payable_days": calculated_metrics.get("payable_days"),
        }
    return {}


def _calculation_unit(name: str) -> str:
    if name.endswith("_days") or name == "cash_conversion_cycle":
        return "days"
    if name in {"net_debt_to_ebitda", "interest_coverage", "cfo_to_pat"}:
        return "multiple"
    return "ratio"


def _input_ids(fact_ids: dict[str, str], names: tuple[str, ...]) -> list[UUID]:
    result: list[UUID] = []
    for name in names:
        raw = fact_ids.get(name)
        if not raw:
            continue
        try:
            result.append(UUID(raw))
        except ValueError:
            continue
    return result


def _metadata_map(facts: dict[str, Any], key: str) -> dict[str, str]:
    raw = facts.get(key)
    if not isinstance(raw, dict):
        return {}
    return {str(name): str(value) for name, value in raw.items() if value is not None}


def _forensic_scores_applicable(context: dict[str, Any]) -> bool:
    security = context.get("security")
    if not isinstance(security, dict):
        return True
    descriptor = " ".join(
        str(security.get(key) or "").lower()
        for key in ("sector", "industry")
    )
    return not any(marker in descriptor for marker in FINANCIAL_SECTOR_MARKERS)


def _period(facts: dict[str, Any]) -> str | None:
    for name in ("revenue_period_end", "pat_period_end", "total_assets_period_end"):
        value = facts.get(name)
        if value:
            return str(value)
    return None


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
