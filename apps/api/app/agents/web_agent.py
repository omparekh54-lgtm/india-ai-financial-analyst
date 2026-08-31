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
    """Converts retrieved web material into traceable evidence without inventing facts."""

    async def run(self, agent_input: AgentInput) -> AgentOutput:
        raw_sources = agent_input.context.get("web_sources") or []
        if not raw_sources:
            return AgentOutput(
                agent=AgentName.WEB,
                ok=False,
                warnings=["No web sources supplied"],
            )

        evidence: list[EvidenceRef] = []
        for raw in raw_sources:
            uri = str(raw.get("url") or raw.get("source_uri") or "").strip()
            if not uri:
                continue
            evidence.append(
                EvidenceRef(
                    source_type=str(raw.get("source_type") or "web"),
                    source_uri=uri,
                    title=raw.get("title"),
                    published_at=raw.get("published_at"),
                    retrieved_at=str(raw.get("retrieved_at") or datetime.now(UTC).isoformat()),
                    freshness=normalize_evidence_freshness(raw.get("freshness") or "near_live"),
                    excerpt=str(raw.get("content") or raw.get("excerpt") or "")[:700] or None,
                    checksum=raw.get("checksum"),
                )
            )

        primary_count = sum(_is_primary_source(item.source_uri) for item in evidence)
        return AgentOutput(
            agent=AgentName.WEB,
            evidence=evidence,
            metrics={
                "source_count": len(evidence),
                "primary_source_count": primary_count,
                "secondary_source_count": len(evidence) - primary_count,
            },
            warnings=[] if primary_count else ["No clearly primary source was present in web evidence"],
        )


def _is_primary_source(uri: str) -> bool:
    lowered = uri.lower()
    primary_domains = (
        "nseindia.com",
        "bseindia.com",
        "sebi.gov.in",
        "rbi.org.in",
        "nsdl.co.in",
        "mca.gov.in",
    )
    return any(domain in lowered for domain in primary_domains)
