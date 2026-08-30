from __future__ import annotations

from statistics import median

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim


class IndustryPeerAgent:
    """Compares normalized company metrics against a supplied peer set."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        peers = agent_input.context.get("peers") or []
        company = agent_input.context.get("company_metrics") or {}
        if not peers:
            return AgentOutput(
                agent=AgentName.INDUSTRY,
                ok=False,
                warnings=["No peer set supplied"],
            )

        metric_names = ("revenue_growth", "ebitda_margin", "roce", "pe", "pb", "ev_ebitda")
        peer_medians: dict[str, float] = {}
        for metric in metric_names:
            values = [_number(peer.get(metric)) for peer in peers]
            clean = [value for value in values if value is not None]
            if clean:
                peer_medians[metric] = float(median(clean))

        relative: dict[str, float] = {}
        claims: list[Claim] = []
        for metric, peer_median in peer_medians.items():
            company_value = _number(company.get(metric))
            if company_value is None:
                continue
            delta = company_value - peer_median
            relative[metric] = delta
            claims.append(
                Claim(
                    agent=AgentName.INDUSTRY,
                    statement=f"{metric} differs from peer median by {delta:.4f}",
                    claim_type="calculation",
                    confidence=1.0,
                    status="verified",
                    data={
                        "metric": metric,
                        "company_value": company_value,
                        "peer_median": peer_median,
                        "difference": delta,
                    },
                )
            )

        return AgentOutput(
            agent=AgentName.INDUSTRY,
            claims=claims,
            metrics={
                "peer_count": len(peers),
                "peer_medians": peer_medians,
                "relative_to_peer_median": relative,
            },
        )


def _number(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
