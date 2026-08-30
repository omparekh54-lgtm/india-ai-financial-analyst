from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import EvidenceRef
from app.core.config import Settings
from app.market.live_overlay import LiveMarketOverlayService
from app.research.context import DatabaseResearchContextLoader
from app.research.filing_evidence import load_exchange_filing_evidence


class UserAwareResearchContextLoader:
    """Loads durable research context, filing evidence, then optional user-authorized live data."""

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
        self.engine = engine
        self.base = DatabaseResearchContextLoader(engine)
        self.live = LiveMarketOverlayService(engine, settings)

    async def load(
        self,
        security_id: UUID,
        *,
        mode: str,
        user_id: UUID | None = None,
    ) -> tuple[dict[str, object], list[EvidenceRef]]:
        context, evidence = await self.base.load(security_id, mode=mode)
        filing_evidence = await load_exchange_filing_evidence(self.engine, security_id)
        if filing_evidence:
            context["parsed_exchange_filing_chunks"] = len(filing_evidence)
            evidence = _dedupe_evidence([*evidence, *filing_evidence])

        security = context.get("security")
        if not isinstance(security, dict):
            return context, evidence
        return await self.live.apply(
            user_id=user_id,
            security_id=security_id,
            security=security,
            context=context,
            evidence=evidence,
        )


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
