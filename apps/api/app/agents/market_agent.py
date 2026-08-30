from __future__ import annotations

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim


class LiveMarketAgent:
    """Code-first market context agent.

    Expected context keys:
    - market_quote: {price, previous_close, volume, average_volume, provider, is_delayed}
    - benchmark: {name, change_pct}
    - sector_benchmark: {name, change_pct}
    """

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        quote = agent_input.context.get("market_quote") or {}
        if not quote:
            return AgentOutput(
                agent=AgentName.MARKET,
                ok=False,
                warnings=["No market quote supplied"],
            )

        price = _number(quote.get("price"))
        previous_close = _number(quote.get("previous_close"))
        volume = _number(quote.get("volume"))
        average_volume = _number(quote.get("average_volume"))
        change_pct = _pct_change(price, previous_close)

        benchmark = agent_input.context.get("benchmark") or {}
        sector = agent_input.context.get("sector_benchmark") or {}
        benchmark_change = _number(benchmark.get("change_pct"))
        sector_change = _number(sector.get("change_pct"))

        metrics = {
            "price": price,
            "change_pct": change_pct,
            "benchmark_change_pct": benchmark_change,
            "sector_change_pct": sector_change,
            "relative_to_benchmark_pct": _subtract(change_pct, benchmark_change),
            "relative_to_sector_pct": _subtract(change_pct, sector_change),
            "volume_ratio": _divide(volume, average_volume),
            "provider": quote.get("provider"),
            "is_delayed": bool(quote.get("is_delayed", True)),
            "as_of": quote.get("as_of"),
        }

        confidence = 0.99 if not metrics["is_delayed"] else 0.90
        claims: list[Claim] = []
        if price is not None:
            claims.append(
                Claim(
                    agent=AgentName.MARKET,
                    statement=f"Market price observed at {price:.4f}",
                    claim_type="fact",
                    confidence=confidence,
                    status="verified",
                    data={"metric": "price", "value": price, "as_of": metrics["as_of"]},
                )
            )
        if change_pct is not None:
            claims.append(
                Claim(
                    agent=AgentName.MARKET,
                    statement=f"Price change versus previous close is {change_pct:.2f}%",
                    claim_type="calculation",
                    confidence=1.0,
                    status="verified",
                    data={"metric": "change_pct", "value": change_pct},
                )
            )

        warnings = []
        if metrics["is_delayed"]:
            warnings.append("Market quote is delayed; do not label it real-time")
        return AgentOutput(agent=AgentName.MARKET, claims=claims, metrics=metrics, warnings=warnings)


def _number(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return (current / previous - 1.0) * 100.0


def _subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _divide(left: float | None, right: float | None) -> float | None:
    if left is None or right in {None, 0}:
        return None
    return left / right
