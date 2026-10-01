from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from scripts import verify_runtime_acquisition as verification


@pytest.mark.asyncio
async def test_verification_cannot_call_unconfigured_provider(monkeypatch, capsys) -> None:
    monkeypatch.setattr(verification, "get_settings", lambda: Settings(
        enable_external_data_calls=False, tavily_api_key="test-key",
    ))
    connector = AsyncMock()
    monkeypatch.setattr(verification, "TavilyConnector", lambda _settings: connector)
    assert await verification.run(0) == 1
    connector.search.assert_not_awaited()
    assert "test-key" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_verification_uses_only_two_bounded_existing_searches(monkeypatch, capsys) -> None:
    monkeypatch.setattr(verification, "get_settings", lambda: Settings(
        enable_external_data_calls=True, tavily_api_key="test-key",
    ))
    connector = AsyncMock()
    connector.search.return_value = []
    monkeypatch.setattr(verification, "TavilyConnector", lambda _settings: connector)
    assert await verification.run(0) == 0
    assert connector.search.await_count == 2
    assert {call.kwargs["topic"] for call in connector.search.await_args_list} == {
        "news", "general",
    }
    assert all(call.kwargs["max_results"] == 3
               for call in connector.search.await_args_list)
    assert "test-key" not in capsys.readouterr().out
