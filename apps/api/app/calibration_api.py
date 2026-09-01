from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthenticatedUser, require_authenticated_user
from app.calibration import CalibrationRepository
from app.core.config import get_settings
from app.db import create_database_engine

router = APIRouter(prefix="/v1/calibration", tags=["calibration"])
settings = get_settings()
CurrentUser = Annotated[AuthenticatedUser, Depends(require_authenticated_user)]


@router.get("/summary")
async def calibration_summary(user: CurrentUser) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    try:
        return await CalibrationRepository(engine).summary_for_user(user.id)
    finally:
        await engine.dispose()
