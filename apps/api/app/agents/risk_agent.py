from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim, EvidenceRef


@dataclass(frozen=True)
class RiskSignal:
    title: str
    severity: str
    statement: str
    data: dict[str, Any]
    evidence_ids: list[UUID] = field(default_factory=list)


class RiskRedFlagAgent:
    """Rule-first India risk screen; thresholds surface signals, not accusations."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        financials = agent_input.context.get("financial_metrics") or {}
        governance = agent_input.context.get("governance") or {}
        signals = (
            _financial_risk_signals(financials)
            + _governance_risk_signals(governance)
            + _filing_risk_signals(agent_input.evidence)
        )
        fallback_evidence_ids = [item.evidence_id for item in agent_input.evidence]

        claims = [
            Claim(
                agent=AgentName.RISK,
                statement=signal.statement,
                claim_type="risk",
                confidence=0.90 if signal.evidence_ids else 0.85 if fallback_evidence_ids else 0.45,
                evidence_ids=signal.evidence_ids or fallback_evidence_ids,
                status="pending",
                data={
                    "title": signal.title,
                    "severity": signal.severity,
                    **signal.data,
                },
            )
            for signal in _dedupe_signals(signals)
        ]
        return AgentOutput(
            agent=AgentName.RISK,
            claims=claims,
            evidence=agent_input.evidence,
            metrics={
                "signal_count": len(claims),
                "high_severity_count": sum(
                    claim.data.get("severity") == "high" for claim in claims
                ),
                "signals": [signal.__dict__ for signal in _dedupe_signals(signals)],
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


def _filing_risk_signals(evidence: list[EvidenceRef]) -> list[RiskSignal]:
    signals: list[RiskSignal] = []
    for item in evidence:
        if item.source_type not in {"exchange_filing", "company_filing", "regulator"}:
            continue
        section = item.section or ""
        text = f"{item.title or ''}\n{item.excerpt or ''}".lower()
        signal = _filing_signal(section, text, item)
        if signal is not None:
            signals.append(signal)
    return signals


def _filing_signal(section: str, text: str, evidence: EvidenceRef) -> RiskSignal | None:
    evidence_ids = [evidence.evidence_id]
    source = {
        "source_uri": evidence.source_uri,
        "page_number": evidence.page_number,
        "event_type": section,
    }
    if section == "auditor_resignation" or ("auditor" in text and "resign" in text):
        return RiskSignal(
            "Auditor resignation filing",
            "high",
            "An official filing reports an auditor resignation; the stated reasons and any audit disagreements require review.",
            source,
            evidence_ids,
        )
    if section in {"cfo_change", "ceo_change", "director_change"} and any(
        word in text for word in ("resign", "cessation")
    ):
        return RiskSignal(
            "Senior management or board departure",
            "medium",
            "An official filing reports a senior management or board departure; timing and stated reasons should be assessed.",
            source,
            evidence_ids,
        )
    if section == "regulatory_action":
        return RiskSignal(
            "Regulatory action disclosure",
            "high",
            "An official filing references regulatory action; the order, financial exposure and remediation should be reviewed.",
            source,
            evidence_ids,
        )
    if section == "litigation":
        return RiskSignal(
            "Material legal or tax disclosure",
            "medium",
            "An official filing references litigation, arbitration, a court order or tax demand requiring materiality assessment.",
            source,
            evidence_ids,
        )
    if section == "credit_rating" and "downgrade" in text:
        return RiskSignal(
            "Credit rating downgrade filing",
            "high",
            "An official filing contains a credit-rating downgrade that may signal increased financing or liquidity risk.",
            source,
            evidence_ids,
        )
    if section == "promoter_pledge":
        return RiskSignal(
            "Promoter pledge or encumbrance disclosure",
            "medium",
            "A promoter pledge or encumbrance filing is present; the direction and current pledged percentage should be verified.",
            source,
            evidence_ids,
        )
    if section == "related_party":
        return RiskSignal(
            "Related-party transaction disclosure",
            "low",
            "A related-party transaction filing is present and should be assessed for size, terms and governance implications.",
            source,
            evidence_ids,
        )
    return None


def _dedupe_signals(signals: list[RiskSignal]) -> list[RiskSignal]:
    output: list[RiskSignal] = []
    seen: set[tuple[str, str | None]] = set()
    for signal in signals:
        uri = str(signal.data.get("source_uri")) if signal.data.get("source_uri") else None
        key = (signal.title, uri)
        if key in seen:
            continue
        seen.add(key)
        output.append(signal)
    return output


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
