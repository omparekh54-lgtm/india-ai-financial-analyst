from __future__ import annotations

from statistics import median
from typing import Any

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim


class IndustryPeerAgent:
    """Compares normalized company metrics against a sourced peer set deterministically."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        peers = agent_input.context.get("peers") or []
        company = agent_input.context.get("company_metrics") or {}
        if not peers:
            return AgentOutput(
                agent=AgentName.INDUSTRY,
                ok=False,
                warnings=["No peer set supplied"],
            )

        peer_evidence = [
            item
            for item in agent_input.evidence
            if item.source_type
            in {
                "peer_metric",
                "security_metric",
                "financial_fact",
                "exchange_filing",
                "company_filing",
            }
        ]
        evidence_ids = [item.evidence_id for item in peer_evidence]
        metric_names = ("revenue_growth", "ebitda_margin", "roce", "pe", "pb", "ev_ebitda")
        peer_medians: dict[str, float] = {}
        peer_counts: dict[str, int] = {}
        for metric in metric_names:
            values = [_number(peer.get(metric)) for peer in peers if isinstance(peer, dict)]
            clean = [value for value in values if value is not None]
            if clean:
                peer_medians[metric] = float(median(clean))
                peer_counts[metric] = len(clean)

        relative: dict[str, float] = {}
        claims: list[Claim] = []
        for metric, peer_median in peer_medians.items():
            company_value = _number(company.get(metric)) if isinstance(company, dict) else None
            if company_value is None:
                continue
            delta = company_value - peer_median
            relative[metric] = delta
            claims.append(
                Claim(
                    agent=AgentName.INDUSTRY,
                    statement=f"{metric} differs from peer median by {delta:.4f}",
                    claim_type="calculation",
                    confidence=0.99 if evidence_ids else 0.45,
                    evidence_ids=evidence_ids,
                    status="pending",
                    metric=f"peer_delta_{metric}",
                    value=delta,
                    unit=_metric_unit(metric),
                    materiality=0.72,
                    calculation_version="industry.peer_median_delta.v2",
                    data={
                        "metric": f"peer_delta_{metric}",
                        "source_metric": metric,
                        "company_value": company_value,
                        "peer_median": peer_median,
                        "difference": delta,
                        "peer_count": peer_counts[metric],
                        "calculation": {
                            "operation": "difference",
                            "left": company_value,
                            "right": peer_median,
                        },
                    },
                )
            )

        warnings = []
        if claims and not evidence_ids:
            warnings.append("Peer calculations are available but source evidence is unavailable")
        return AgentOutput(
            agent=AgentName.INDUSTRY,
            claims=claims,
            evidence=peer_evidence,
            metrics={
                "peer_count": len(peers),
                "peer_medians": peer_medians,
                "peer_metric_counts": peer_counts,
                "relative_to_peer_median": relative,
            },
            warnings=warnings,
        )


def _metric_unit(metric: str) -> str:
    if metric in {"revenue_growth", "ebitda_margin", "roce"}:
        return "ratio"
    if metric in {"pe", "pb", "ev_ebitda"}:
        return "multiple"
    return ""


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
