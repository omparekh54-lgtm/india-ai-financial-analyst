from __future__ import annotations

from app.core.config import Settings
from app.providers.client import ChatMessage, ChatResult, ProviderCallError, build_client
from app.providers.router import Capability, ProviderRouter


class ProviderGateway:
    """Provider facade with deterministic fallback order and no secret exposure."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.router = ProviderRouter(settings)

    @property
    def enabled(self) -> bool:
        return self.settings.enable_external_llm_calls

    async def complete(
        self,
        capability: Capability,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> ChatResult:
        errors: list[str] = []
        for choice in self.router.candidates(capability):
            if choice.provider in {"disabled", "unavailable"}:
                raise ProviderCallError(choice.reason)
            try:
                client = build_client(choice.provider, self.settings)
                return await client.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except ProviderCallError as exc:
                errors.append(f"{choice.provider}: {exc}")

        raise ProviderCallError("All configured providers failed: " + "; ".join(errors))
