from __future__ import annotations

from dataclasses import dataclass
from re import Pattern
from re import compile as re_compile

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim, EvidenceRef


@dataclass(frozen=True)
class FilingEventRule:
    event_type: str
    pattern: Pattern[str]
    materiality: float
    claim_type: str = "fact"


EVENT_RULES = [
    FilingEventRule("auditor_resignation", re_compile(r"auditor.{0,80}(resign|cessation)", 2), 0.98, "risk"),
    FilingEventRule("cfo_change", re_compile(r"(chief financial officer|\bcfo\b).{0,80}(resign|appoint|change|cessation)", 2), 0.92),
    FilingEventRule("ceo_change", re_compile(r"(chief executive officer|\bceo\b|managing director).{0,80}(resign|appoint|change|cessation)", 2), 0.90),
    FilingEventRule("director_change", re_compile(r"director.{0,80}(resign|appoint|change|cessation)", 2), 0.85),
    FilingEventRule("credit_rating", re_compile(r"credit rating|rating (upgrade|downgrade|reaffirm|revision)", 2), 0.90),
    FilingEventRule("promoter_pledge", re_compile(r"promoter.{0,100}(pledge|encumbrance)", 2), 0.95, "risk"),
    FilingEventRule("financial_results", re_compile(r"financial results|quarterly results|regulation 33", 2), 0.98),
    FilingEventRule("earnings_call", re_compile(r"earnings call|conference call|concall", 2), 0.85),
    FilingEventRule("earnings_transcript", re_compile(r"earnings call transcript|concall transcript|transcript", 2), 0.90),
    FilingEventRule("investor_presentation", re_compile(r"investor presentation|analyst presentation", 2), 0.85),
    FilingEventRule("annual_report", re_compile(r"annual report|regulation 34", 2), 0.95),
    FilingEventRule("dividend", re_compile(r"dividend|record date", 2), 0.78),
    FilingEventRule("buyback", re_compile(r"buy[- ]?back", 2), 0.92),
    FilingEventRule("bonus", re_compile(r"bonus (issue|share)", 2), 0.88),
    FilingEventRule("split", re_compile(r"stock split|sub[- ]?division of.*share|share split", 2), 0.88),
    FilingEventRule("qip", re_compile(r"qualified institutional placement|\bqip\b", 2), 0.90),
    FilingEventRule("preferential_issue", re_compile(r"preferential (issue|allotment)|issue of warrants", 2), 0.88),
    FilingEventRule("rights_issue", re_compile(r"rights issue|right issue", 2), 0.85),
    FilingEventRule("related_party", re_compile(r"related party transaction|\brpt\b", 2), 0.88, "risk"),
    FilingEventRule("merger_demerger", re_compile(r"merger|demerger|scheme of arrangement|amalgamation", 2), 0.92),
    FilingEventRule("acquisition_disposal", re_compile(r"acquisition|divestment|disposal|sale of stake", 2), 0.82),
    FilingEventRule("order_win", re_compile(r"award of order|receipt of order|order win|letter of award", 2), 0.82, "catalyst"),
    FilingEventRule("regulatory_action", re_compile(r"sebi.{0,80}(order|penalty|notice)|regulatory action", 2), 0.95, "risk"),
    FilingEventRule("litigation", re_compile(r"litigation|court order|arbitration|legal proceeding|tax demand", 2), 0.82, "risk"),
]

_RULE_BY_EVENT = {rule.event_type: rule for rule in EVENT_RULES}


class FilingsGovernanceAgent:
    """Classifies material filing events from already-ingested primary evidence."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        filing_evidence = [
            item
            for item in agent_input.evidence
            if item.source_type in {"exchange_filing", "company_filing", "regulator"}
        ]
        if not filing_evidence:
            return AgentOutput(
                agent=AgentName.FILINGS,
                ok=False,
                warnings=["No primary filing evidence was supplied"],
            )

        claims: list[Claim] = []
        detected_events: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        for evidence in filing_evidence:
            rules = _rules_for_evidence(evidence)
            for rule in rules:
                key = (rule.event_type, str(evidence.evidence_id))
                if key in seen:
                    continue
                seen.add(key)
                statement = _event_statement(rule.event_type, evidence)
                claims.append(
                    Claim(
                        agent=AgentName.FILINGS,
                        statement=statement,
                        claim_type=rule.claim_type,  # type: ignore[arg-type]
                        confidence=rule.materiality,
                        evidence_ids=[evidence.evidence_id],
                        status="pending",
                        data={
                            "event_type": rule.event_type,
                            "materiality": rule.materiality,
                            "source_uri": evidence.source_uri,
                            "page_number": evidence.page_number,
                        },
                    )
                )
                detected_events.append(
                    {
                        "event_type": rule.event_type,
                        "materiality": rule.materiality,
                        "evidence_id": str(evidence.evidence_id),
                        "page_number": evidence.page_number,
                    }
                )

        return AgentOutput(
            agent=AgentName.FILINGS,
            claims=claims,
            evidence=filing_evidence,
            metrics={
                "filing_evidence_count": len(filing_evidence),
                "detected_event_count": len(detected_events),
                "events": detected_events,
            },
        )


def _rules_for_evidence(evidence: EvidenceRef) -> list[FilingEventRule]:
    if evidence.section and evidence.section in _RULE_BY_EVENT:
        return [_RULE_BY_EVENT[evidence.section]]
    text = f"{evidence.title or ''}\n{evidence.excerpt or ''}"
    return [rule for rule in EVENT_RULES if rule.pattern.search(text)]


def _event_statement(event_type: str, evidence: EvidenceRef) -> str:
    label = event_type.replace("_", " ")
    title = evidence.title or "primary filing"
    page = f" page {evidence.page_number}" if evidence.page_number else ""
    return f"Detected {label} disclosure in {title}{page}."
