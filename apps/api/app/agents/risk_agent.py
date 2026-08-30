from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim


@dataclass(frozen=True)
class RiskSignal:
    title: str
    severity: str
    statement: str
    data: dict[str, Any]


class RiskRedFlagAgent:
    """Rule-first India risk screen; thresholds surface signals, not accusations."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        financials = agent_input.context.get("financial_metrics") or {}
        governance = agent_input.context.get("governance") or {}
        signals = _financial_risk_signals(financials) + _governance_risk_signals(governance)
        evidence_ids = [item.evidence_id for item in agent_input.evidence]

        claims = [
            Claim(
                agent=AgentName.RISK,
                statement=signal.statement,
                claim_type="risk",
                confidence=0.85 if evidence_ids else 0.45,
                evidence_ids=evidence_ids,
                status="pending",
                data={
                    "title": signal.title,
                    "severity": signal.severity,
                    **signal.data,
                },
            )
            for signal in signals
        ]
        return AgentOutput(
            agent=AgentName.RISK,
            claims=claims,
            evidence=agent_input.evidence,
            metrics={
                "signal_count": len(signals),
                "high_severity_count": sum(signal.severity == "high" for signal in signals),
                "signals": [signal.__dict__ for signal in signals],
            },
        )


def _financial_risk_signals(metrics: dict[str, Any]) -> list[RiskSignal]:
    signals: list[RiskSignal] = []
    cfo_to_pat = _number(metrics.get("cfo_to_pat"))
    if cfo_to_pat is not None and cfo_to_pat < 0.7:
        signals.append(
            RiskSignal(
                "Weak cash conversion",
                "medium",
                "Operating cash flow is materially below reported profit, warranting earnings-quality review.",
                {"cfo_to_pat": cfo_to_pat},
            )
        )

    leverage = _number(metrics.get("net_debt_to_ebitda"))
    if leverage is not None and leverage > 3.0:
        signals.append(
            RiskSignal(
                "Elevated leverage",
                "high" if leverage > 4.0 else "medium",
                "Net debt relative to EBITDA is elevated and should be tested against sector norms and maturities.",
                {"net_debt_to_ebitda": leverage},
            )
        )

    coverage = _number(metrics.get("interest_coverage"))
    if coverage is not None and coverage < 2.5:
        signals.append(
            RiskSignal(
                "Thin interest coverage",
                "high" if coverage < 1.5 else "medium",
                "Interest coverage is thin, increasing sensitivity to earnings or financing-cost pressure.",
                {"interest_coverage": coverage},
            )
        )

    ccc = _number(metrics.get("cash_conversion_cycle"))
    previous_ccc = _number(metrics.get("previous_cash_conversion_cycle"))
    if ccc is not None and previous_ccc is not None and ccc - previous_ccc > 20:
        signals.append(
            RiskSignal(
                "Working-capital deterioration",
                "medium",
                "The cash-conversion cycle has lengthened materially versus the prior comparison period.",
                {"cash_conversion_cycle": ccc, "previous_cash_conversion_cycle": previous_ccc},
            )
        )
    return signals


def _governance_risk_signals(governance: dict[str, Any]) -> list[RiskSignal]:
    signals: list[RiskSignal] = []
    pledge = _number(governance.get("promoter_pledge_pct"))
    if pledge is not None and pledge > 0:
        signals.append(
            RiskSignal(
                "Promoter pledge present",
                "high" if pledge >= 25 else "medium",
                "Promoter shares are pledged; trend, purpose and financing counterparties require review.",
                {"promoter_pledge_pct": pledge},
            )
        )

    if governance.get("auditor_resignation_recent") is True:
        signals.append(
            RiskSignal(
                "Recent auditor resignation",
                "high",
                "A recent auditor resignation is a governance event requiring primary-filing review.",
                {},
            )
        )
    if governance.get("credit_rating_downgrade_recent") is True:
        signals.append(
            RiskSignal(
                "Recent credit-rating downgrade",
                "high",
                "A recent credit-rating downgrade may indicate weakening credit quality or liquidity.",
                {},
            )
        )
    return signals


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
