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


def _engine_and_repository() -> tuple[AsyncEngine, WatchlistRepository]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    return engine, WatchlistRepository(engine)


@router.get("")
async def list_watchlists(user: CurrentUser) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    try:
        watchlists = await repository.list_for_user(user.id)
    finally:
        await engine.dispose()
    return {"count": len(watchlists), "watchlists": watchlists}


@router.post("", status_code=201)
async def create_watchlist(
    request: WatchlistCreateRequest,
    user: CurrentUser,
) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    try:
        try:
            watchlist = await repository.create(user.id, request.name)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="A watchlist with that name already exists",
            ) from exc
    finally:
        await engine.dispose()
    return watchlist


@router.delete("/{watchlist_id}")
async def delete_watchlist(watchlist_id: UUID, user: CurrentUser) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    try:
        removed = await repository.delete(user.id, watchlist_id)
    finally:
        await engine.dispose()
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
        try:
            item = await repository.add_item(
                user.id,
                watchlist_id,
                request.security_id,
                notes=request.notes,
                event_research_enabled=request.event_research_enabled,
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=404,
                detail="Security not found",
            ) from exc
    finally:
        await engine.dispose()
    if item is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    return item


@router.delete("/{watchlist_id}/items/{security_id}")
async def remove_watchlist_item(
    watchlist_id: UUID,
    security_id: UUID,
    user: CurrentUser,
) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    try:
        removed = await repository.remove_item(user.id, watchlist_id, security_id)
    finally:
        await engine.dispose()
    if not removed:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    return {
        "watchlist_id": str(watchlist_id),
        "security_id": str(security_id),
        "deleted": True,
    }
