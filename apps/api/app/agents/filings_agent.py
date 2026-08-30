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
    FilingEventRule("auditor_resignation", re_compile(r"auditor.{0,40}resign", 2), 0.95, "risk"),
    FilingEventRule("cfo_change", re_compile(r"(chief financial officer|\bcfo\b).{0,50}(resign|appoint|change)", 2), 0.85),
    FilingEventRule("ceo_change", re_compile(r"(chief executive officer|\bceo\b).{0,50}(resign|appoint|change)", 2), 0.85),
    FilingEventRule("credit_rating", re_compile(r"credit rating|rating (upgrade|downgrade|reaffirm)", 2), 0.85),
    FilingEventRule("promoter_pledge", re_compile(r"promoter.{0,80}(pledge|encumbrance)", 2), 0.90, "risk"),
    FilingEventRule("financial_results", re_compile(r"financial results|quarterly results|regulation 33", 2), 0.95),
    FilingEventRule("earnings_call", re_compile(r"earnings call|conference call|concall transcript", 2), 0.80),
    FilingEventRule("investor_presentation", re_compile(r"investor presentation", 2), 0.75),
    FilingEventRule("dividend", re_compile(r"dividend|record date", 2), 0.75),
    FilingEventRule("buyback", re_compile(r"buy[- ]?back", 2), 0.90),
    FilingEventRule("bonus", re_compile(r"bonus (issue|share)", 2), 0.85),
    FilingEventRule("split", re_compile(r"stock split|sub[- ]?division of.*share", 2), 0.85),
    FilingEventRule("qip", re_compile(r"qualified institutional placement|\bqip\b", 2), 0.85),
    FilingEventRule("preferential_issue", re_compile(r"preferential (issue|allotment)", 2), 0.85),
    FilingEventRule("related_party", re_compile(r"related party transaction", 2), 0.80, "risk"),
    FilingEventRule("merger_demerger", re_compile(r"merger|demerger|scheme of arrangement", 2), 0.90),
    FilingEventRule("order_win", re_compile(r"award of order|receipt of order|order win", 2), 0.75, "catalyst"),
]


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
            text = evidence.excerpt or ""
            for rule in EVENT_RULES:
                if not rule.pattern.search(text):
                    continue
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


def _event_statement(event_type: str, evidence: EvidenceRef) -> str:
    label = event_type.replace("_", " ")
    title = evidence.title or "primary filing"
    return f"Detected {label} disclosure in {title}."
