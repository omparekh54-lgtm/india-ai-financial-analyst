from __future__ import annotations

from statistics import median
from typing import Any

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.calculations.valuation import DcfAssumptions, discounted_cash_flow
from app.valuation.router import ValuationMethod, choose_valuation_methods


class ValuationScenarioAgent:
    """Routes valuation by sector and computes only methods backed by available real inputs."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        raw = agent_input.context.get("valuation_inputs") or {}
        data = dict(raw) if isinstance(raw, dict) else {}
        _enrich_from_context(data, agent_input.context)
        if not data:
            return AgentOutput(
                agent=AgentName.VALUATION,
                ok=False,
                warnings=["No valuation inputs supplied"],
            )

        sector = str(data.get("sector") or "")
        industry = " ".join(
            value
            for value in (
                str(data.get("industry") or ""),
                str(data.get("business_type") or ""),
            )
            if value
        )
        route = choose_valuation_methods(sector, industry)
        candidates = [*route.primary, *route.secondary]

        selected: ValuationMethod | None = None
        scenarios: dict[str, float] | None = None
        unavailable: list[dict[str, object]] = []
        for method in candidates:
            missing = _missing_inputs(method, data)
            if missing:
                unavailable.append({"method": method.value, "missing_inputs": missing})
                continue
            try:
                scenarios = _calculate(method, data)
            except (TypeError, ValueError, KeyError) as exc:
                unavailable.append({"method": method.value, "error": str(exc)})
                continue
            selected = method
            break

        route_metrics = {
            "sector_family": route.sector_family,
            "primary_methods": [method.value for method in route.primary],
            "secondary_methods": [method.value for method in route.secondary],
            "route_reason": route.reason,
            "unavailable_methods": unavailable,
        }
        if selected is None or scenarios is None:
            return AgentOutput(
                agent=AgentName.VALUATION,
                ok=False,
                metrics=route_metrics,
                warnings=[
                    (
                        "No sector-appropriate valuation method had complete factual and "
                        "assumption inputs; the engine did not invent missing assumptions."
                    )
                ],
            )

        current_price = _number(data.get("current_price"))
        base_value = scenarios.get("base")
        upside_pct = None
        if current_price is not None and current_price != 0 and base_value is not None:
            upside_pct = (base_value / current_price - 1.0) * 100.0

        evidence_ids = [item.evidence_id for item in agent_input.evidence]
        method_label = _method_label(selected)
        claim = Claim(
            agent=AgentName.VALUATION,
            statement=f"Bear/base/bull values calculated using {method_label}",
            claim_type="scenario",
            confidence=0.9 if evidence_ids else 0.75,
            evidence_ids=evidence_ids,
            status="pending",
            data={
                "method": method_label,
                "method_code": selected.value,
                "sector_family": route.sector_family,
                "scenarios": scenarios,
                "upside_pct": upside_pct,
            },
        )
        warnings = [] if evidence_ids else ["Valuation calculation has no linked input evidence"]
        return AgentOutput(
            agent=AgentName.VALUATION,
            claims=[claim],
            evidence=agent_input.evidence,
            metrics={
                **route_metrics,
                "method": method_label,
                "method_code": selected.value,
                "scenarios": scenarios,
                "current_price": current_price,
                "base_case_upside_pct": upside_pct,
                "input_provenance": {
                    "target_pe": data.get("target_pe"),
                    "target_pb": data.get("target_pb"),
                    "target_ev_ebitda": data.get("target_ev_ebitda"),
                    "target_pe_source": data.get("target_pe_source"),
                    "target_pb_source": data.get("target_pb_source"),
                    "target_ev_ebitda_source": data.get("target_ev_ebitda_source"),
                },
            },
            warnings=warnings,
        )


def _enrich_from_context(data: dict[str, object], context: dict[str, object]) -> None:
    financials_value = context.get("financials")
    financials = financials_value if isinstance(financials_value, dict) else {}
    for sources, target in (
        (("eps", "earnings_per_share", "diluted_eps"), "earnings_per_share"),
        (("ebitda",), "ebitda"),
        (("net_debt",), "net_debt"),
        (("shares_outstanding",), "shares_outstanding"),
        (("nav_per_share",), "nav_per_share"),
        (("distribution_per_share", "dividend_per_share"), "distribution_per_share"),
    ):
        if data.get(target) is not None:
            continue
        for source in sources:
            value = financials.get(source)
            if value is not None:
                data[target] = value
                break

    peers_value = context.get("peers")
    peers = peers_value if isinstance(peers_value, list) else []
    for metric, target in (
        ("pe", "target_pe"),
        ("pb", "target_pb"),
        ("ev_ebitda", "target_ev_ebitda"),
    ):
        if data.get(target) is not None:
            continue
        values = [
            parsed
            for peer in peers
            if isinstance(peer, dict)
            for parsed in [_number(peer.get(metric))]
            if parsed is not None and parsed > 0
        ]
        if values:
            data[target] = median(values)
            data[f"{target}_source"] = "peer_median"
            data[f"{target}_peer_count"] = len(values)


def _missing_inputs(method: ValuationMethod, data: dict[str, object]) -> list[str]:
    requirements: dict[ValuationMethod, tuple[str, ...]] = {
        ValuationMethod.DCF: (
            "base_fcf",
            "growth_rates",
            "wacc",
            "terminal_growth",
            "net_debt",
            "shares_outstanding",
        ),
        ValuationMethod.PE: ("earnings_per_share", "target_pe"),
        ValuationMethod.PB: ("book_value_per_share", "target_pb"),
        ValuationMethod.RESIDUAL_INCOME: (
            "book_value_per_share",
            "sustainable_roe",
            "cost_of_equity",
            "terminal_growth",
        ),
        ValuationMethod.PRICE_TO_EMBEDDED_VALUE: ("embedded_value_per_share", "target_pev"),
        ValuationMethod.EV_EBITDA: (
            "ebitda",
            "target_ev_ebitda",
            "net_debt",
            "shares_outstanding",
        ),
        ValuationMethod.EV_SALES: ("sales_per_share", "target_ev_sales"),
        ValuationMethod.NAV: ("nav_per_share",),
        ValuationMethod.SOTP: ("sotp_value_per_share",),
        ValuationMethod.DIVIDEND_YIELD: ("distribution_per_share", "target_yield"),
    }
    return [key for key in requirements[method] if data.get(key) is None]


def _calculate(method: ValuationMethod, data: dict[str, object]) -> dict[str, float]:
    if method == ValuationMethod.PB:
        return _multiple_scenarios(_required(data, "book_value_per_share"), _required(data, "target_pb"))
    if method == ValuationMethod.PE:
        return _multiple_scenarios(_required(data, "earnings_per_share"), _required(data, "target_pe"))
    if method == ValuationMethod.PRICE_TO_EMBEDDED_VALUE:
        return _multiple_scenarios(
            _required(data, "embedded_value_per_share"),
            _required(data, "target_pev"),
        )
    if method == ValuationMethod.EV_SALES:
        return _multiple_scenarios(_required(data, "sales_per_share"), _required(data, "target_ev_sales"))
    if method == ValuationMethod.EV_EBITDA:
        ebitda = _required(data, "ebitda")
        target = _required(data, "target_ev_ebitda")
        net_debt = _required(data, "net_debt")
        shares = _required(data, "shares_outstanding")
        if shares <= 0:
            raise ValueError("shares_outstanding must be positive")
        return {
            label: (ebitda * target * multiplier - net_debt) / shares
            for label, multiplier in (("bear", 0.85), ("base", 1.0), ("bull", 1.15))
        }
    if method == ValuationMethod.SOTP:
        base = _required(data, "sotp_value_per_share")
        discount = _number(data.get("holding_company_discount")) or 0.0
        adjusted = base * (1.0 - discount)
        return {"bear": adjusted * 0.85, "base": adjusted, "bull": adjusted * 1.15}
    if method == ValuationMethod.NAV:
        nav = _required(data, "nav_per_share")
        discount = _number(data.get("nav_discount")) or 0.0
        base = nav * (1.0 - discount)
        return {"bear": base * 0.9, "base": base, "bull": base * 1.1}
    if method == ValuationMethod.DIVIDEND_YIELD:
        distribution = _required(data, "distribution_per_share")
        target_yield = _required(data, "target_yield")
        if target_yield <= 0:
            raise ValueError("target_yield must be positive")
        base = distribution / target_yield
        return {"bear": base * 0.9, "base": base, "bull": base * 1.1}
    if method == ValuationMethod.RESIDUAL_INCOME:
        book = _required(data, "book_value_per_share")
        roe = _required(data, "sustainable_roe")
        cost = _required(data, "cost_of_equity")
        growth = _required(data, "terminal_growth")
        return {
            "bear": _justified_book_value(book, roe - 0.02, cost + 0.01, growth),
            "base": _justified_book_value(book, roe, cost, growth),
            "bull": _justified_book_value(book, roe + 0.02, max(growth + 0.01, cost - 0.01), growth),
        }

    growth_values = data.get("growth_rates", [])
    if not isinstance(growth_values, (list, tuple)):
        raise TypeError("growth_rates must be a list or tuple")
    growth_rates: list[float] = []
    for value in growth_values:
        parsed = _number(value)
        if parsed is None:
            raise ValueError("growth_rates must contain only numeric values")
        growth_rates.append(parsed)

    assumptions = DcfAssumptions(
        base_fcf=_required(data, "base_fcf"),
        growth_rates=growth_rates,
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


def _justified_book_value(book: float, roe: float, cost: float, growth: float) -> float:
    if cost <= growth:
        raise ValueError("cost_of_equity must exceed terminal_growth")
    return book * (roe - growth) / (cost - growth)


def _multiple_scenarios(per_share_metric: float, target_multiple: float) -> dict[str, float]:
    return {
        "bear": per_share_metric * target_multiple * 0.85,
        "base": per_share_metric * target_multiple,
        "bull": per_share_metric * target_multiple * 1.15,
    }


def _method_label(method: ValuationMethod) -> str:
    labels = {
        ValuationMethod.DCF: "dcf",
        ValuationMethod.PE: "price_to_earnings",
        ValuationMethod.PB: "price_to_book",
        ValuationMethod.RESIDUAL_INCOME: "residual_income",
        ValuationMethod.PRICE_TO_EMBEDDED_VALUE: "price_to_embedded_value",
        ValuationMethod.EV_EBITDA: "ev_to_ebitda",
        ValuationMethod.EV_SALES: "ev_to_sales",
        ValuationMethod.NAV: "nav",
        ValuationMethod.SOTP: "sotp",
        ValuationMethod.DIVIDEND_YIELD: "dividend_yield",
    }
    return labels[method]


def _required(data: dict[str, object], key: str) -> float:
    value = _number(data.get(key))
    if value is None:
        raise ValueError(f"missing {key}")
    return value


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
