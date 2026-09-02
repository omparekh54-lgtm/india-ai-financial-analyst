from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth import AuthenticatedUser, require_authenticated_user
from app.comparison import ComparisonRepository, MetricFilter
from app.core.config import get_settings
from app.db import create_database_engine

router = APIRouter(prefix="/v1/intelligence", tags=["intelligence"])
settings = get_settings()
CurrentUser = Annotated[AuthenticatedUser, Depends(require_authenticated_user)]


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    security_ids: list[UUID] = Field(min_length=2, max_length=5)
    metrics: list[str] | None = Field(default=None, max_length=24)


class ScreenMetricFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric_name: str = Field(min_length=1, max_length=64)
    min_value: float | None = None
    max_value: float | None = None


class ScreenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: list[ScreenMetricFilter] = Field(min_length=1, max_length=5)
    sector: str | None = Field(default=None, max_length=100)
    industry: str | None = Field(default=None, max_length=120)
    sort_metric: str | None = Field(default=None, max_length=64)
    descending: bool = True
    limit: int = Field(default=50, ge=1, le=100)


def _engine_and_repository():  # type: ignore[no-untyped-def]
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    return engine, ComparisonRepository(engine)


@router.post("/compare")
async def compare_companies(request: CompareRequest, _user: CurrentUser) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    try:
        try:
            return await repository.compare(request.security_ids, metric_names=request.metrics)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        await engine.dispose()


@router.post("/screen")
async def screen_companies(request: ScreenRequest, _user: CurrentUser) -> dict[str, object]:
    engine, repository = _engine_and_repository()
    filters = [
        MetricFilter(
            metric_name=item.metric_name,
            min_value=item.min_value,
            max_value=item.max_value,
        )
        for item in request.filters
    ]
    try:
        try:
            return await repository.screen(
                filters=filters,
                sector=request.sector,
                industry=request.industry,
                sort_metric=request.sort_metric,
                descending=request.descending,
                limit=request.limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        await engine.dispose()
