from __future__ import annotations

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.calculations.valuation import DcfAssumptions, discounted_cash_flow


class ValuationScenarioAgent:
    """Routes valuation method by business type and calculates scenarios in Python."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        data = agent_input.context.get("valuation_inputs") or {}
        if not data:
            return AgentOutput(
                agent=AgentName.VALUATION,
                ok=False,
                warnings=["No valuation inputs supplied"],
            )

        method = _select_method(str(data.get("sector") or data.get("business_type") or ""))
        try:
            scenarios = _calculate(method, data)
        except (TypeError, ValueError, KeyError) as exc:
            return AgentOutput(
                agent=AgentName.VALUATION,
                ok=False,
                errors=[f"Valuation inputs invalid for {method}: {exc}"],
                metrics={"method": method},
            )

        current_price = _number(data.get("current_price"))
        base_value = scenarios.get("base")
        upside_pct = None
        if current_price not in {None, 0} and base_value is not None:
            upside_pct = (base_value / current_price - 1.0) * 100.0

        claim = Claim(
            agent=AgentName.VALUATION,
            statement=f"Base-case value calculated using {method}",
            claim_type="scenario",
            confidence=0.85,
            status="supported",
            data={"method": method, "scenarios": scenarios, "upside_pct": upside_pct},
        )
        return AgentOutput(
            agent=AgentName.VALUATION,
            claims=[claim],
            metrics={
                "method": method,
                "scenarios": scenarios,
                "current_price": current_price,
                "base_case_upside_pct": upside_pct,
            },
        )


def _select_method(sector: str) -> str:
    value = sector.lower()
    if any(term in value for term in ("bank", "nbfc", "financial services", "lending")):
        return "price_to_book"
    if "insurance" in value:
        return "price_to_embedded_value"
    if any(term in value for term in ("holding", "conglomerate")):
        return "sotp"
    if any(term in value for term in ("loss making", "pre-profit", "internet platform")):
        return "ev_to_sales"
    return "dcf"


def _calculate(method: str, data: dict[str, object]) -> dict[str, float]:
    if method == "price_to_book":
        bvps = _required(data, "book_value_per_share")
        target = _required(data, "target_pb")
        return _multiple_scenarios(bvps, target)
    if method == "price_to_embedded_value":
        evps = _required(data, "embedded_value_per_share")
        target = _required(data, "target_pev")
        return _multiple_scenarios(evps, target)
    if method == "ev_to_sales":
        sales_per_share = _required(data, "sales_per_share")
        target = _required(data, "target_ev_sales")
        return _multiple_scenarios(sales_per_share, target)
    if method == "sotp":
        base = _required(data, "sotp_value_per_share")
        discount = _number(data.get("holding_company_discount")) or 0.0
        adjusted = base * (1.0 - discount)
        return {"bear": adjusted * 0.85, "base": adjusted, "bull": adjusted * 1.15}

    assumptions = DcfAssumptions(
        base_fcf=_required(data, "base_fcf"),
        growth_rates=[float(value) for value in data.get("growth_rates", [])],
        wacc=_required(data, "wacc"),
        terminal_growth=_required(data, "terminal_growth"),
        net_debt=_required(data, "net_debt"),
        shares_outstanding=_required(data, "shares_outstanding"),
    )
    base = discounted_cash_flow(assumptions).value_per_share
    bear_assumptions = DcfAssumptions(
        base_fcf=assumptions.base_fcf,
        growth_rates=[growth - 0.02 for growth in assumptions.growth_rates],
        wacc=assumptions.wacc + 0.01,
        terminal_growth=max(0.0, assumptions.terminal_growth - 0.005),
        net_debt=assumptions.net_debt,
        shares_outstanding=assumptions.shares_outstanding,
    )
    bull_assumptions = DcfAssumptions(
        base_fcf=assumptions.base_fcf,
        growth_rates=[growth + 0.02 for growth in assumptions.growth_rates],
        wacc=max(assumptions.terminal_growth + 0.01, assumptions.wacc - 0.01),
        terminal_growth=assumptions.terminal_growth + 0.005,
        net_debt=assumptions.net_debt,
        shares_outstanding=assumptions.shares_outstanding,
    )
    return {
        "bear": discounted_cash_flow(bear_assumptions).value_per_share,
        "base": base,
        "bull": discounted_cash_flow(bull_assumptions).value_per_share,
    }


def _multiple_scenarios(per_share_metric: float, target_multiple: float) -> dict[str, float]:
    return {
        "bear": per_share_metric * target_multiple * 0.85,
        "base": per_share_metric * target_multiple,
        "bull": per_share_metric * target_multiple * 1.15,
    }


def _required(data: dict[str, object], key: str) -> float:
    value = _number(data.get(key))
    if value is None:
        raise ValueError(f"missing {key}")
    return value


def _number(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
