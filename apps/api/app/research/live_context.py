from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import EvidenceRef
from app.core.config import Settings
from app.market.live_overlay import LiveMarketOverlayService
from app.research.context import DatabaseResearchContextLoader


class UserAwareResearchContextLoader:
    """Loads durable research context first, then overlays optional user-authorized live data."""

    def __init__(self, engine: AsyncEngine, settings: Settings) -> None:
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
