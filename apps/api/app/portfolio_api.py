from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.auth import AuthenticatedUser, require_authenticated_user
from app.core.config import get_settings
from app.db import create_database_engine
from app.portfolio import PortfolioRepository

router = APIRouter(prefix="/v1/portfolios", tags=["portfolios"])
settings = get_settings()
CurrentUser = Annotated[AuthenticatedUser, Depends(require_authenticated_user)]


class PortfolioCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    base_currency: str = Field(default="INR", min_length=3, max_length=3, pattern="^[A-Za-z]{3}$")


class PortfolioPositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    security_id: UUID
    quantity: float = Field(gt=0)
    average_cost: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=1000)


def _repository() -> tuple[object, PortfolioRepository]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    return engine, PortfolioRepository(engine)


@router.get("")
async def list_portfolios(user: CurrentUser) -> dict[str, object]:
    engine, repository = _repository()
    try:
        portfolios = await repository.list_for_user(user.id)
    finally:
        await engine.dispose()  # type: ignore[union-attr]
    return {"count": len(portfolios), "portfolios": portfolios}


@router.post("", status_code=201)
async def create_portfolio(request: PortfolioCreateRequest, user: CurrentUser) -> dict[str, object]:
    engine, repository = _repository()
    try:
        try:
            portfolio = await repository.create(user.id, request.name, request.base_currency)
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A portfolio with that name already exists") from exc
    finally:
        await engine.dispose()  # type: ignore[union-attr]
    return portfolio


@router.delete("/{portfolio_id}")
async def delete_portfolio(portfolio_id: UUID, user: CurrentUser) -> dict[str, object]:
    engine, repository = _repository()
    try:
        deleted = await repository.delete(user.id, portfolio_id)
    finally:
        await engine.dispose()  # type: ignore[union-attr]
    if not deleted:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"portfolio_id": str(portfolio_id), "deleted": True}


@router.post("/{portfolio_id}/positions")
async def upsert_portfolio_position(
    portfolio_id: UUID,
    request: PortfolioPositionRequest,
    user: CurrentUser,
) -> dict[str, object]:
    engine, repository = _repository()
    try:
        try:
            position = await repository.upsert_position(
                user.id,
                portfolio_id,
                request.security_id,
                quantity=request.quantity,
                average_cost=request.average_cost,
                notes=request.notes,
            )
        except IntegrityError as exc:
            raise HTTPException(status_code=404, detail="Security not found") from exc
    finally:
        await engine.dispose()  # type: ignore[union-attr]
    if position is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return position


@router.delete("/{portfolio_id}/positions/{security_id}")
async def remove_portfolio_position(
    portfolio_id: UUID,
    security_id: UUID,
    user: CurrentUser,
) -> dict[str, object]:
    engine, repository = _repository()
    try:
        deleted = await repository.remove_position(user.id, portfolio_id, security_id)
    finally:
        await engine.dispose()  # type: ignore[union-attr]
    if not deleted:
        raise HTTPException(status_code=404, detail="Portfolio position not found")
    return {"portfolio_id": str(portfolio_id), "security_id": str(security_id), "deleted": True}


@router.get("/{portfolio_id}/analysis")
async def portfolio_analysis(portfolio_id: UUID, user: CurrentUser) -> dict[str, object]:
    engine, repository = _repository()
    try:
        analysis = await repository.analyze(user.id, portfolio_id)
    finally:
        await engine.dispose()  # type: ignore[union-attr]
    if analysis is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return analysis
