from __future__ import annotations

import pytest

from app.core.config import Settings
from app.providers.client import ChatMessage, ChatResult, ProviderCallError
from app.providers.gateway import ProviderGateway
from app.providers.router import Capability


class _FailingClient:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResult:
        del messages, temperature, max_tokens
        raise ProviderCallError(
            f"{self.provider} rate limited",
            status_code=429,
            retryable=True,
        )


class _SuccessfulClient:
    def __init__(self, provider: str) -> None:
        self.provider = provider

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResult:
        del messages, temperature, max_tokens
        return ChatResult(provider=self.provider, model="test", content="ok")


@pytest.mark.asyncio
async def test_gateway_opens_circuit_and_uses_free_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        _env_file=None,
        enable_external_llm_calls=True,
        free_only=True,
        groq_api_key="configured",
        cerebras_api_key="configured",
        provider_circuit_failure_threshold=1,
        provider_circuit_cooldown_seconds=60,
    )
    calls: list[str] = []

    def fake_build_client(provider: str, _settings: Settings):
        calls.append(provider)
        if provider == "groq":
            return _FailingClient(provider)
        return _SuccessfulClient(provider)

    monkeypatch.setattr("app.providers.gateway.build_client", fake_build_client)
    gateway = ProviderGateway(settings)
    messages = [ChatMessage(role="user", content="test")]

    first = await gateway.complete(Capability.FAST_REASONING, messages)
    second = await gateway.complete(Capability.FAST_REASONING, messages)

    assert first.provider == "cerebras"
    assert second.provider == "cerebras"
    assert calls == ["groq", "cerebras", "cerebras"]
