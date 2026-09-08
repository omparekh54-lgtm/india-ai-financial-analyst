from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db import database_health


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("off", True), ("on", False), (None, False)])
async def test_database_health_requires_writable_connection(mode, expected):
    connection = AsyncMock()
    connection.scalar.return_value = mode
    engine = MagicMock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    assert await database_health(engine) is expected


@pytest.mark.asyncio
async def test_database_health_masks_connection_failure():
    engine = MagicMock()
    engine.connect.side_effect = RuntimeError("private connection credentials")
    assert await database_health(engine) is False
