from __future__ import annotations

from datetime import UTC, datetime

from app.agents.contracts import (
    AgentInput,
    AgentName,
    AgentOutput,
    EvidenceRef,
    normalize_evidence_freshness,
)


class WebIntelligenceAgent:
    """Converts retrieved web material into graded traceable evidence without inventing facts."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        raw_sources = agent_input.context.get("web_sources") or []
        if not raw_sources:
            return AgentOutput(
                agent=AgentName.WEB,
                ok=False,
                warnings=["No web sources supplied"],
            )

        evidence: list[EvidenceRef] = []
        seen: set[str] = set()
        for raw in raw_sources:
            if not isinstance(raw, dict):
                continue
            uri = str(raw.get("url") or raw.get("source_uri") or "").strip()
            if not uri or uri in seen:
                continue
            seen.add(uri)
            evidence.append(
                EvidenceRef(
                    source_type=str(raw.get("source_type") or "web"),
                    source_uri=uri,
                    title=raw.get("title"),
                    published_at=raw.get("published_at"),
                    retrieved_at=str(raw.get("retrieved_at") or datetime.now(UTC).isoformat()),
                    freshness=normalize_evidence_freshness(raw.get("freshness") or "near_live"),
                    excerpt=str(raw.get("content") or raw.get("excerpt") or "")[:900] or None,
                    checksum=raw.get("checksum"),
                    source_priority=_source_priority(raw, uri),
                )
            )

        primary_count = sum(item.source_priority == 1 for item in evidence)
        company_count = sum(item.source_priority == 2 for item in evidence)
        return AgentOutput(
            agent=AgentName.WEB,
            evidence=evidence,
            metrics={
                "source_count": len(evidence),
                "primary_source_count": primary_count,
                "company_source_count": company_count,
                "secondary_source_count": len(evidence) - primary_count - company_count,
                "source_tier_counts": {
                    tier: sum(item.source_tier == tier for item in evidence)
                    for tier in ("A", "B", "C", "D", "E")
                },
            },
            warnings=[] if primary_count else ["No Tier-A primary source was present in web evidence"],
        )


def _source_priority(raw: dict[str, object], uri: str) -> int:
    source_type = str(raw.get("source_type") or "").lower()
    lowered = uri.lower()
    if source_type in {"exchange_filing", "regulator", "official_macro", "official_flow"} or any(
        domain in lowered
        for domain in (
            "nseindia.com",
            "bseindia.com",
            "sebi.gov.in",
            "rbi.org.in",
            "nsdl.co.in",
            "mca.gov.in",
        )
    ):
        return 1
    if source_type in {"company_ir", "company_filing", "earnings_release"}:
        return 2
    if source_type in {"social", "community", "forum"}:
        return 5
    if source_type in {"blog", "general_web"}:
        return 4
    return 3
