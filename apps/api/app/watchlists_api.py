import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import AuthenticatedUser, require_authenticated_user
from app.core.config import get_settings
from app.db import create_database_engine
from app.repositories.watchlists import WatchlistRepository
from app.securities.repository import SecurityMasterRepository
from app.securities.resolver import SecurityResolver

router = APIRouter(prefix="/v1/watchlists", tags=["watchlists"])
settings = get_settings()
CurrentUser = Annotated[AuthenticatedUser, Depends(require_authenticated_user)]


class WatchlistCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)


class WatchlistItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    security_id: UUID
    notes: str | None = Field(default=None, max_length=1000)
    event_research_enabled: bool = True


class WatchlistResolvedItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=160)
    notes: str | None = Field(default=None, max_length=1000)
    event_research_enabled: bool = True


# Postgres SQLSTATE codes. A bare IntegrityError catch cannot tell these apart, which
# previously reported foreign-key failures to users as "that name already exists".
_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"
_CHECK_VIOLATION = "23514"
_NOT_NULL_VIOLATION = "23502"

logger = logging.getLogger(__name__)


def _sqlstate(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None) or getattr(
        getattr(exc, "orig", None), "pgcode", None
    )


def _translate_integrity_error(
    exc: IntegrityError,
    *,
    unique_detail: str,
    foreign_key_detail: str,
) -> HTTPException:
    """Map an IntegrityError to an honest status code based on the actual constraint.

    A unique violation is the user's fault and is safe to explain. A foreign-key or
    not-null violation means the request referenced something that does not exist, or
    that server-side ownership wiring is broken; never report either as a duplicate name.
    """
    code = _sqlstate(exc)
    if code == _UNIQUE_VIOLATION:
        return HTTPException(status_code=409, detail=unique_detail)
    if code == _FOREIGN_KEY_VIOLATION:
        return HTTPException(status_code=404, detail=foreign_key_detail)
    if code == _CHECK_VIOLATION:
        return HTTPException(status_code=422, detail="Request violates a database constraint")
    if code == _NOT_NULL_VIOLATION:
        logger.error("Not-null violation writing watchlist data", exc_info=exc)
        return HTTPException(status_code=500, detail="Internal error")
    logger.error("Unclassified integrity error (sqlstate=%s)", code, exc_info=exc)
    return HTTPException(status_code=500, detail="Internal error")


def _engine_and_repository() -> tuple[AsyncEngine, WatchlistRepository]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    return engine, WatchlistRepository(engine)


@router.get("")
async def list_watchlists(user: CurrentUser) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    watchlists = await repository.list_for_user(user.id)
    return {"count": len(watchlists), "watchlists": watchlists}


@router.post("", status_code=201)
async def create_watchlist(
    request: WatchlistCreateRequest,
    user: CurrentUser,
) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    try:
        watchlist = await repository.create(user.id, request.name)
    except IntegrityError as exc:
        raise _translate_integrity_error(
            exc,
            unique_detail="A watchlist with that name already exists",
            foreign_key_detail="Watchlist owner could not be resolved",
        ) from exc
    return watchlist


@router.delete("/{watchlist_id}")
async def delete_watchlist(watchlist_id: UUID, user: CurrentUser) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    removed = await repository.delete(user.id, watchlist_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return {"watchlist_id": str(watchlist_id), "deleted": True}


@router.post("/{watchlist_id}/items")
async def add_watchlist_item(
    watchlist_id: UUID,
    request: WatchlistItemRequest,
    user: CurrentUser,
) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    try:
        item = await repository.add_item(
            user.id,
            watchlist_id,
            request.security_id,
            notes=request.notes,
            event_research_enabled=request.event_research_enabled,
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(
            exc,
            unique_detail="That security is already on this watchlist",
            foreign_key_detail="Security not found",
        ) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return item


@router.post("/{watchlist_id}/items/resolve")
async def add_watchlist_item_by_query(
    watchlist_id: UUID,
    request: WatchlistResolvedItemRequest,
    user: CurrentUser,
) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    securities = await SecurityMasterRepository(engine).list_all()
    resolution = SecurityResolver(securities).resolve(request.query)
    candidate = resolution.candidate
    if not resolution.resolved or candidate is None or candidate.security.id is None:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Security query could not be resolved confidently",
                "normalized_query": resolution.normalized_query,
                "alternatives": [
                    {
                        "legal_name": item.security.legal_name,
                        "nse_symbol": item.security.nse_symbol,
                        "bse_code": item.security.bse_code,
                        "isin": item.security.isin,
                        "score": round(item.score, 4),
                        "match_reason": item.match_reason,
                    }
                    for item in resolution.alternatives
                ],
            },
        )
    try:
        item = await repository.add_item(
            user.id,
            watchlist_id,
            candidate.security.id,
            notes=request.notes,
            event_research_enabled=request.event_research_enabled,
        )
    except IntegrityError as exc:
        raise _translate_integrity_error(
            exc,
            unique_detail="That security is already on this watchlist",
            foreign_key_detail="Security not found",
        ) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return {
        **item,
        "security": candidate.security.model_dump(mode="json"),
        "resolution": {
            "score": candidate.score,
            "match_reason": candidate.match_reason,
        },
    }


@router.delete("/{watchlist_id}/items/{security_id}")
async def remove_watchlist_item(
    watchlist_id: UUID,
    security_id: UUID,
    user: CurrentUser,
) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    removed = await repository.remove_item(user.id, watchlist_id, security_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    return {
        "watchlist_id": str(watchlist_id),
        "security_id": str(security_id),
        "deleted": True,
    }
