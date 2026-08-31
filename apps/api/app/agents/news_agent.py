from __future__ import annotations

import re
from datetime import UTC, datetime

from app.agents.contracts import AgentInput, AgentName, AgentOutput, Claim, EvidenceRef

HIGH_IMPACT = (
    "earnings",
    "results",
    "guidance",
    "merger",
    "acquisition",
    "demerger",
    "fraud",
    "sebi",
    "default",
    "rating downgrade",
    "auditor resignation",
    "promoter pledge",
    "buyback",
    "qip",
)
MEDIUM_IMPACT = (
    "order win",
    "contract",
    "capacity",
    "launch",
    "partnership",
    "stake",
    "dividend",
    "block deal",
    "bulk deal",
)


class NewsEventAgent:
    """Deduplicates normalized events and emits evidence-linked claims for Agent 15 to validate."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        raw_events = agent_input.context.get("news_events") or []
        if not raw_events:
            return AgentOutput(
                agent=AgentName.NEWS,
                ok=False,
                warnings=["No news events supplied"],
            )

        seen: set[str] = set()
        events: list[dict[str, object]] = []
        evidence: list[EvidenceRef] = []
        claims: list[Claim] = []

        for raw in raw_events:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or raw.get("source_uri") or "").strip()
            if not title:
                continue
            key = _normalize_title(title)
            if key in seen:
                continue
            seen.add(key)
            materiality = _materiality(title + " " + str(raw.get("summary") or ""))
            priority = _source_priority(raw, url)
            event = {
                "title": title,
                "url": url,
                "published_at": raw.get("published_at"),
                "source": raw.get("source"),
                "materiality": materiality,
                "source_priority": priority,
            }
            events.append(event)

            if not url:
                continue
            item = EvidenceRef(
                source_type="news",
                source_uri=url,
                title=title,
                published_at=raw.get("published_at"),
                retrieved_at=str(raw.get("retrieved_at") or datetime.now(UTC).isoformat()),
                freshness="near_live",
                excerpt=str(raw.get("summary") or "")[:700] or None,
                checksum=raw.get("checksum"),
                source_priority=priority,
            )
            evidence.append(item)
            if materiality in {"high", "medium"}:
                numeric_materiality = 0.90 if materiality == "high" else 0.68
                claims.append(
                    Claim(
                        agent=AgentName.NEWS,
                        statement=f"Material news event detected: {title}",
                        claim_type="catalyst" if materiality == "medium" else "risk",
                        confidence=0.78 if materiality == "medium" else 0.84,
                        evidence_ids=[item.evidence_id],
                        # Specialists never self-approve; Agent 15 owns the final status.
                        status="pending",
                        materiality=numeric_materiality,
                        freshness_at=item.published_at or item.retrieved_at,
                        data={
                            "materiality": materiality,
                            "title": title,
                            "source_priority": priority,
                        },
                    )
                )

        events.sort(
            key=lambda event: {"high": 2, "medium": 1, "low": 0}[str(event["materiality"])],
            reverse=True,
        )
        return AgentOutput(
            agent=AgentName.NEWS,
            claims=claims,
            evidence=evidence,
            metrics={
                "event_count": len(events),
                "high_materiality_count": sum(event["materiality"] == "high" for event in events),
                "events": events,
            },
        )


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _materiality(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in HIGH_IMPACT):
        return "high"
    if any(term in lowered for term in MEDIUM_IMPACT):
        return "medium"
    return "low"


def _source_priority(raw: dict[str, object], url: str) -> int:
    source_type = str(raw.get("source_type") or raw.get("source") or "").lower()
    lowered = url.lower()
    if source_type in {"exchange_filing", "regulator", "official"} or any(
        domain in lowered
        for domain in ("nseindia.com", "bseindia.com", "sebi.gov.in", "rbi.org.in")
    ):
        return 1
    if source_type in {"company_ir", "company_filing", "earnings_release"}:
        return 2
    if source_type in {"social", "community", "forum"}:
        return 5
    return 3
