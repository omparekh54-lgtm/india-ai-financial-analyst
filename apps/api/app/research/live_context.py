from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import EvidenceRef
from app.core.config import Settings
from app.evidence.embeddings import EmbeddingError, build_embedding_provider
from app.evidence.semantic import SemanticEvidenceRetriever, build_research_queries
from app.market.live_overlay import LiveMarketOverlayService
from app.research.acquisition import FreshResearchAcquisitionService
from app.research.context import DatabaseResearchContextLoader
from app.research.filing_evidence import load_exchange_filing_evidence


class UserAwareResearchContextLoader:
    """Loads durable context, fresh research, filing evidence and optional live market data."""

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.settings = settings
        self.base = DatabaseResearchContextLoader(engine)
        self.acquisition = FreshResearchAcquisitionService(engine, settings)
        self.live = LiveMarketOverlayService(engine, settings)
        embedder = build_embedding_provider(settings)
        self.semantic = (
            SemanticEvidenceRetriever(
                engine,
                embedder,
                min_similarity=settings.semantic_evidence_min_similarity,
            )
            if embedder is not None
            else None
        )

    async def load(
        self,
        security_id: UUID,
        *,
        mode: str,
        depth: str,
        user_id: UUID | None = None,
    ) -> tuple[dict[str, object], list[EvidenceRef]]:
        context, evidence = await self.base.load(security_id, mode=mode)
        context["analysis_depth"] = depth
        recent_filing_evidence = await load_exchange_filing_evidence(self.engine, security_id)
        semantic_filing_evidence: list[EvidenceRef] = []
        semantic_per_query, semantic_max_results = _semantic_budget(self.settings, depth)

        security = context.get("security")
        if self.semantic is not None and isinstance(security, dict):
            try:
                matches = await self.semantic.search(
                    security_id,
                    build_research_queries(
                        user_query=str(
                            security.get("legal_name") or security.get("nse_symbol") or ""
                        ),
                        security=security,
                        mode=mode,
                    ),
                    per_query=semantic_per_query,
                    max_results=semantic_max_results,
                )
            except (EmbeddingError, SQLAlchemyError):
                context["semantic_evidence"] = {
                    "enabled": True,
                    "status": "degraded",
                    "match_count": 0,
                    "depth": depth,
                }
            else:
                semantic_filing_evidence = [match.evidence for match in matches]
                context["semantic_evidence"] = {
                    "enabled": True,
                    "status": "ready",
                    "match_count": len(matches),
                    "top_similarity": round(matches[0].similarity, 4) if matches else None,
                    "queries": len({match.query for match in matches}),
                    "per_query_budget": semantic_per_query,
                    "max_results_budget": semantic_max_results,
                    "depth": depth,
                }
        else:
            context["semantic_evidence"] = {
                "enabled": False,
                "status": "disabled",
                "match_count": 0,
                "depth": depth,
            }

        filing_evidence = _dedupe_evidence([*semantic_filing_evidence, *recent_filing_evidence])
        if filing_evidence:
            context["parsed_exchange_filing_chunks"] = len(filing_evidence)
            evidence = _dedupe_evidence([*evidence, *filing_evidence])

        financials = context.get("financials")
        if isinstance(financials, dict):
            earnings = _build_earnings_context(financials, filing_evidence)
            if earnings:
                context["earnings"] = earnings

        if not isinstance(security, dict):
            return context, evidence

        context, evidence = await self.acquisition.enrich(
            security_id=security_id,
            security=security,
            mode=mode,
            depth=depth,
            context=context,
            evidence=evidence,
        )
        return await self.live.apply(
            user_id=user_id,
            security_id=security_id,
            security=security,
            context=context,
            evidence=evidence,
        )


def _semantic_budget(settings: Settings, depth: str) -> tuple[int, int]:
    per_query = max(1, settings.semantic_evidence_per_query)
    max_results = max(1, settings.semantic_evidence_max_chunks)
    if depth == "quick":
        return max(1, per_query // 2), max(3, max_results // 2)
    if depth == "deep":
        return min(per_query * 2, 8), min(max_results * 2, 40)
    return per_query, max_results


def _build_earnings_context(
    financials: dict[str, object],
    filing_evidence: list[EvidenceRef],
) -> dict[str, object]:
    earnings: dict[str, object] = {}
    mappings = {
        "revenue": "revenue",
        "previous_revenue": "prior_revenue",
        "pat": "pat",
        "previous_pat": "prior_pat",
        "ebitda": "ebitda",
        "previous_ebitda": "prior_ebitda",
    }
    for source_key, target_key in mappings.items():
        value = financials.get(source_key)
        if value is not None:
            earnings[target_key] = value

    period = (
        financials.get("revenue_period_end")
        or financials.get("pat_period_end")
        or financials.get("ebitda_period_end")
    )
    if period is not None:
        earnings["period"] = period

    result_evidence = [item for item in filing_evidence if item.section == "financial_results"]
    if result_evidence:
        earnings["published_at"] = result_evidence[0].published_at

    commentary_chunks = [
        item.excerpt
        for item in filing_evidence
        if item.section in {"earnings_call", "earnings_transcript", "investor_presentation"}
        and item.excerpt
    ]
    if commentary_chunks:
        earnings["management_commentary"] = "\n\n".join(commentary_chunks)[:16000]

    return earnings


def _dedupe_evidence(items: list[EvidenceRef]) -> list[EvidenceRef]:
    seen: set[tuple[str, str, int | None, str | None]] = set()
    output: list[EvidenceRef] = []
    for item in items:
        key = (item.source_type, item.source_uri, item.page_number, item.checksum)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output
