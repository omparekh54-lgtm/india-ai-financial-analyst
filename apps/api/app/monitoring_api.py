from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import AuthenticatedUser, require_authenticated_user
from app.core.config import get_settings
from app.db import create_database_engine
from app.research.monitoring import MonitoringRepository

router = APIRouter(prefix="/v1/monitoring", tags=["monitoring"])
settings = get_settings()
CurrentUser = Annotated[AuthenticatedUser, Depends(require_authenticated_user)]


@router.get("/alerts")
async def monitoring_alerts(
    user: CurrentUser,
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    try:
        alerts = await MonitoringRepository(engine).list_alerts(
            user.id,
            unread_only=unread_only,
            limit=limit,
        )
    finally:
        await engine.dispose()
    return {
        "count": len(alerts),
        "unread_only": unread_only,
        "alerts": alerts,
    }


@router.post("/alerts/{alert_id}/read")
async def mark_monitoring_alert_read(alert_id: UUID, user: CurrentUser) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    try:
        updated = await MonitoringRepository(engine).mark_read(user.id, alert_id)
    finally:
        await engine.dispose()
    if not updated:
        raise HTTPException(status_code=404, detail="Monitoring alert not found")
    return {"alert_id": str(alert_id), "read": True}


@router.get("/securities/{security_id}/delta")
async def security_monitoring_delta(
    security_id: UUID,
    user: CurrentUser,
) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    try:
        payload = await MonitoringRepository(engine).latest_delta_for_user(user.id, security_id)
    finally:
        await engine.dispose()
    if payload.get("snapshot") is None:
        raise HTTPException(status_code=404, detail="No completed research snapshot found")
    return payload
