from __future__ import annotations

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim


class LiveMarketAgent:
    """Code-first market context agent with explicit live/delayed semantics."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        quote = agent_input.context.get("market_quote") or {}
        if not quote:
            return AgentOutput(
                agent=AgentName.MARKET,
                ok=False,
                warnings=["No market quote supplied"],
            )

        market_evidence = [item for item in agent_input.evidence if item.source_type == "market_data"]
        evidence_ids = [item.evidence_id for item in market_evidence]
        price = _number(quote.get("price"))
        previous_close = _number(quote.get("previous_close"))
        volume = _number(quote.get("volume"))
        average_volume = _number(quote.get("average_volume"))
        change_pct = _pct_change(price, previous_close)

        benchmark = agent_input.context.get("benchmark") or {}
        sector = agent_input.context.get("sector_benchmark") or {}
        benchmark_change = _number(benchmark.get("change_pct"))
        sector_change = _number(sector.get("change_pct"))
        delayed = bool(quote.get("is_delayed", True))

        metrics = {
            "price": price,
            "change_pct": change_pct,
            "benchmark_change_pct": benchmark_change,
            "sector_change_pct": sector_change,
            "relative_to_benchmark_pct": _subtract(change_pct, benchmark_change),
            "relative_to_sector_pct": _subtract(change_pct, sector_change),
            "volume_ratio": _divide(volume, average_volume),
            "provider": quote.get("provider"),
            "is_delayed": delayed,
            "as_of": quote.get("as_of"),
        }

        confidence = 0.99 if not delayed else 0.90
        claims: list[Claim] = []
        if price is not None:
            label = "Latest stored market close" if delayed else "Live market price"
            claims.append(
                Claim(
                    agent=AgentName.MARKET,
                    statement=f"{label} observed at {price:.4f}",
                    claim_type="fact",
                    confidence=confidence,
                    evidence_ids=evidence_ids,
                    status="pending",
                    data={
                        "metric": "price",
                        "value": price,
                        "as_of": metrics["as_of"],
                        "is_delayed": delayed,
                        "requires_current_data": not delayed,
                    },
                )
            )
        if change_pct is not None:
            claims.append(
                Claim(
                    agent=AgentName.MARKET,
                    statement=f"Price change versus previous stored close is {change_pct:.2f}%",
                    claim_type="calculation",
                    confidence=0.99,
                    evidence_ids=evidence_ids,
                    status="pending",
                    data={"metric": "change_pct", "value": change_pct, "is_delayed": delayed},
                )
            )

        warnings = []
        if delayed:
            warnings.append("Market quote is delayed; it is not labeled real-time")
        if not evidence_ids:
            warnings.append("No market-data evidence reference was available")
        return AgentOutput(
            agent=AgentName.MARKET,
            claims=claims,
            evidence=market_evidence,
            metrics=metrics,
            warnings=warnings,
        )


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in {None, 0}:
        return None
    return (current / previous - 1.0) * 100.0


def _subtract(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _divide(left: float | None, right: float | None) -> float | None:
    return None if left is None or right in {None, 0} else left / right
