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
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or raw.get("source_uri") or "").strip()
            if not title:
                continue
            key = _normalize_title(title)
            if key in seen:
                continue
            seen.add(key)
            materiality = _materiality(title + " " + str(raw.get("summary") or ""))
            event = {
                "title": title,
                "url": url,
                "published_at": raw.get("published_at"),
                "source": raw.get("source"),
                "materiality": materiality,
            }
            events.append(event)

            if url:
                item = EvidenceRef(
                    source_type="news",
                    source_uri=url,
                    title=title,
                    published_at=raw.get("published_at"),
                    retrieved_at=datetime.now(UTC).isoformat(),
                    freshness="near_live",
                    excerpt=str(raw.get("summary") or "")[:500] or None,
                )
                evidence.append(item)
                if materiality in {"high", "medium"}:
                    claims.append(
                        Claim(
                            agent=AgentName.NEWS,
                            statement=f"Material news event detected: {title}",
                            claim_type="catalyst" if materiality == "medium" else "risk",
                            confidence=0.80 if materiality == "medium" else 0.88,
                            evidence_ids=[item.evidence_id],
                            status="supported",
                            data={"materiality": materiality, "title": title},
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
