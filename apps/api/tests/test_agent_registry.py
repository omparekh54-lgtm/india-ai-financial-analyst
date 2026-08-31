from __future__ import annotations

from typing import cast

from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.contracts import AgentName
from app.core.config import Settings
from app.orchestration.registry import build_agent_registry


def test_complete_16_role_architecture_has_every_runtime_handler() -> None:
    engine = cast(AsyncEngine, object())
    registry = build_agent_registry(engine, Settings())

    assert len(AgentName) == 16
    assert AgentName.ORCHESTRATOR not in registry.handlers
    assert set(registry.handlers) == set(AgentName) - {AgentName.ORCHESTRATOR}
    assert len(registry.handlers) == 15
    assert AgentName.VALIDATOR in registry.handlers
    assert AgentName.SYNTHESIS in registry.handlers
