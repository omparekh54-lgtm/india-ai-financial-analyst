from __future__ import annotations

from typing import Any

import pandas as pd

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim
from app.calculations.technicals import atr, macd, realized_volatility, rsi


class TechnicalDerivativesAgent:
    """Deterministic technicals plus optional derivatives metrics from normalized broker data."""

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
        period = str(frame.iloc[-1].get("ts") or "latest_bar")

        macd_frame = macd(close)
        metrics: dict[str, object] = {
            "rsi_14": _last_valid(rsi(close)),
            "macd": _last_valid(macd_frame["macd"]),
            "macd_signal": _last_valid(macd_frame["signal"]),
            "atr_14": _last_valid(atr(high, low, close)),
            "realized_volatility_20d": _last_valid(realized_volatility(close)),
        }
        derivatives = _derivatives_metrics(agent_input.context.get("derivatives"))
        if derivatives:
            metrics["derivatives"] = derivatives

        evidence = [
            item
            for item in agent_input.evidence
            if item.source_type == "market_data" or "derivative" in item.source_type.lower()
        ]
        evidence_ids = [item.evidence_id for item in evidence]
        claims = [
            Claim(
                agent=AgentName.TECHNICAL,
                statement=f"{name} calculated as {value:.4f}",
                claim_type="calculation",
                confidence=0.99,
                evidence_ids=evidence_ids,
                status="pending",
                metric=name,
                value=float(value),
                period=period,
                calculation_version="technical.indicator.v1",
                data={"metric": name, "value": value},
            )
            for name, value in metrics.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        for name, value in derivatives.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            claims.append(
                Claim(
                    agent=AgentName.TECHNICAL,
                    statement=f"Derivatives metric {name} calculated as {value:.4f}",
                    claim_type="calculation",
                    confidence=0.97,
                    evidence_ids=evidence_ids,
                    status="pending",
                    metric=name,
                    value=float(value),
                    period=period,
                    calculation_version="technical.derivatives.v1",
                    data={"metric": name, "value": value, "category": "derivatives"},
                )
            )

        warnings: list[str] = []
        if not evidence_ids:
            warnings.append("Technical calculations lack market-data provenance")
        if derivatives and not any("derivative" in item.source_type.lower() for item in evidence):
            warnings.append(
                "Derivatives calculations are present but no derivatives-specific evidence is linked"
            )
        return AgentOutput(
            agent=AgentName.TECHNICAL,
            claims=claims,
            evidence=evidence,
            metrics=metrics,
            warnings=warnings,
        )


def _derivatives_metrics(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}

    metrics: dict[str, object] = {}
    spot = _number(value.get("spot_price"))
    futures = value.get("futures")
    futures_data = futures if isinstance(futures, dict) else {}
    futures_price = _number(futures_data.get("price"))
    if spot is not None and spot != 0.0 and futures_price is not None:
        metrics["futures_basis_pct"] = (futures_price / spot - 1.0) * 100.0

    open_interest = _number(futures_data.get("open_interest"))
    previous_open_interest = _number(futures_data.get("previous_open_interest"))
    if (
        open_interest is not None
        and previous_open_interest is not None
        and previous_open_interest != 0.0
    ):
        metrics["futures_oi_change_pct"] = (
            open_interest / previous_open_interest - 1.0
        ) * 100.0
    rollover = _number(futures_data.get("rollover_pct"))
    if rollover is not None:
        metrics["rollover_pct"] = rollover

    options_value = value.get("options")
    options = (
        [item for item in options_value if isinstance(item, dict)]
        if isinstance(options_value, list)
        else []
    )
    if not options:
        return metrics

    call_oi = _sum_open_interest(options, "call")
    put_oi = _sum_open_interest(options, "put")
    if call_oi > 0.0:
        metrics["put_call_oi_ratio"] = put_oi / call_oi

    if spot is not None:
        nearest_strike = _nearest_strike(options, spot)
        if nearest_strike is not None:
            atm_options = [
                option
                for option in options
                if _number(option.get("strike")) == nearest_strike
            ]
            ivs = [
                parsed
                for option in atm_options
                for parsed in [_number(option.get("implied_volatility"))]
                if parsed is not None and parsed >= 0.0
            ]
            if ivs:
                metrics["atm_implied_volatility"] = sum(ivs) / float(len(ivs))
            metrics.update(_atm_greeks(atm_options))

        max_pain = _max_pain_strike(options)
        if max_pain is not None:
            metrics["max_pain_strike"] = max_pain
            if spot != 0.0:
                metrics["max_pain_distance_pct"] = (max_pain / spot - 1.0) * 100.0

    return metrics


def _sum_open_interest(options: list[dict[str, Any]], side: str) -> float:
    total = 0.0
    for option in options:
        if _option_type(option) != side:
            continue
        parsed = _number(option.get("open_interest"))
        if parsed is not None and parsed >= 0.0:
            total += parsed
    return total


def _atm_greeks(options: list[dict[str, Any]]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for side in ("call", "put"):
        matching = [option for option in options if _option_type(option) == side]
        if not matching:
            continue
        option = matching[0]
        for greek in ("delta", "gamma", "theta", "vega"):
            parsed = _number(option.get(greek))
            if parsed is not None:
                metrics[f"atm_{side}_{greek}"] = parsed
    return metrics


def _nearest_strike(options: list[dict[str, Any]], spot: float) -> float | None:
    strikes: set[float] = set()
    for option in options:
        strike = _number(option.get("strike"))
        if strike is not None:
            strikes.add(strike)
    if not strikes:
        return None
    return min(strikes, key=lambda strike: abs(strike - spot))


def _max_pain_strike(options: list[dict[str, Any]]) -> float | None:
    strike_values: set[float] = set()
    for option in options:
        strike = _number(option.get("strike"))
        if strike is not None:
            strike_values.add(strike)
    strikes = sorted(strike_values)
    if not strikes:
        return None

    pain_by_settlement: dict[float, float] = {}
    for settlement in strikes:
        total = 0.0
        for option in options:
            strike = _number(option.get("strike"))
            open_interest = _number(option.get("open_interest"))
            if strike is None or open_interest is None or open_interest < 0.0:
                continue
            if _option_type(option) == "call":
                total += max(settlement - strike, 0.0) * open_interest
            elif _option_type(option) == "put":
                total += max(strike - settlement, 0.0) * open_interest
        pain_by_settlement[settlement] = total
    return min(pain_by_settlement, key=lambda settlement: pain_by_settlement[settlement])


def _option_type(option: dict[str, Any]) -> str:
    raw = str(option.get("option_type") or option.get("type") or "").lower()
    if raw in {"ce", "call", "c"}:
        return "call"
    if raw in {"pe", "put", "p"}:
        return "put"
    return ""


def _last_valid(series: pd.Series) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
