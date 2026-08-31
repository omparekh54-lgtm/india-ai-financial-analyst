from __future__ import annotations

from time import monotonic

from app.core.config import Settings
from app.providers.client import ChatMessage, ChatResult, ProviderCallError, build_client
from app.providers.router import Capability, ProviderRouter


class ProviderGateway:
    """Provider facade with FREE_ONLY routing, fallback and bounded circuit breaking."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.router = ProviderRouter(settings)
        self._failure_counts: dict[str, int] = {}
        self._blocked_until: dict[str, float] = {}

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
        attempted = 0
        for choice in self.router.candidates(capability):
            if choice.provider in {"disabled", "unavailable"}:
                raise ProviderCallError(choice.reason, retryable=False)
            if self._circuit_open(choice.provider):
                errors.append(f"{choice.provider}: circuit open")
                continue

            attempted += 1
            try:
                client = build_client(choice.provider, self.settings)
                result = await client.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self._record_success(choice.provider)
                return result
            except ProviderCallError as exc:
                errors.append(f"{choice.provider}: {exc}")
                if exc.retryable:
                    self._record_failure(choice.provider)

        if attempted == 0 and errors:
            raise ProviderCallError(
                "All eligible providers are temporarily circuit-blocked: " + "; ".join(errors),
                retryable=True,
            )
        raise ProviderCallError(
            "All configured providers failed: " + "; ".join(errors),
            retryable=True,
        )

    def _circuit_open(self, provider: str) -> bool:
        until = self._blocked_until.get(provider)
        if until is None:
            return False
        if monotonic() >= until:
            self._blocked_until.pop(provider, None)
            self._failure_counts.pop(provider, None)
            return False
        return True

    def _record_failure(self, provider: str) -> None:
        failures = self._failure_counts.get(provider, 0) + 1
        self._failure_counts[provider] = failures
        if failures >= self.settings.provider_circuit_failure_threshold:
            self._blocked_until[provider] = (
                monotonic() + self.settings.provider_circuit_cooldown_seconds
            )

    def _record_success(self, provider: str) -> None:
        self._failure_counts.pop(provider, None)
        self._blocked_until.pop(provider, None)
