from __future__ import annotations

import asyncio
import json

from app.core.config import get_settings
from app.core.preflight import database_preflight
from app.core.readiness import audit_settings
from app.db import create_database_engine


async def main() -> int:
    settings = get_settings()
    config = audit_settings(settings)
    payload: dict[str, object] = {
        "configuration": config.as_dict(),
        "database": {
            "ready": False,
            "connected": False,
            "error_type": "DatabaseNotConfigured",
        },
    }

    if settings.database_url:
        engine = create_database_engine(settings.database_url)
        try:
            database = await database_preflight(engine)
            payload["database"] = database.as_dict()
        finally:
            await engine.dispose()

    database_payload = payload["database"]
    database_ready = bool(
        isinstance(database_payload, dict) and database_payload.get("ready") is True
    )
    ready = config.ready and database_ready
    payload["ready"] = ready
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
