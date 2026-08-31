from __future__ import annotations

import asyncio
from math import ceil
from time import monotonic

from app.core.config import Settings
from app.providers.client import ChatMessage, ChatResult, ProviderCallError, build_client
from app.providers.router import Capability, ProviderRouter


class ProviderGateway:
    """Provider facade with FREE_ONLY routing, fallback, circuit breaking and job budgets."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.router = ProviderRouter(settings)
        self._failure_counts: dict[str, int] = {}
        self._blocked_until: dict[str, float] = {}
        self._budget_lock = asyncio.Lock()
        self._job_usage: dict[str, dict[str, object]] = {}

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
        budget_key: str | None = None,
    ) -> ChatResult:
        errors: list[str] = []
        attempted = 0
        for choice in self.router.candidates(capability):
            if choice.provider in {"disabled", "unavailable"}:
                raise ProviderCallError(choice.reason, retryable=False)
            if self._circuit_open(choice.provider):
                errors.append(f"{choice.provider}: circuit open")
                continue

            if budget_key is not None:
                await self._reserve_job_budget(
                    budget_key,
                    provider=choice.provider,
                    messages=messages,
                    max_tokens=max_tokens,
                )

            attempted += 1
            try:
                client = build_client(choice.provider, self.settings)
                result = await client.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self._record_success(choice.provider)
                if budget_key is not None:
                    await self._record_actual_usage(budget_key, result)
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

    def job_usage(self, budget_key: str) -> dict[str, object]:
        usage = self._job_usage.get(budget_key) or {}
        provider_attempts = usage.get("provider_attempts")
        return {
            "calls": int(usage.get("calls") or 0),
            "reserved_tokens": int(usage.get("reserved_tokens") or 0),
            "actual_tokens": int(usage.get("actual_tokens") or 0),
            "provider_attempts": (
                dict(provider_attempts) if isinstance(provider_attempts, dict) else {}
            ),
            "max_calls": self.settings.llm_max_calls_per_job,
            "max_reserved_tokens": self.settings.llm_max_reserved_tokens_per_job,
        }

    async def _reserve_job_budget(
        self,
        budget_key: str,
        *,
        provider: str,
        messages: list[ChatMessage],
        max_tokens: int,
    ) -> None:
        # Conservative estimate: four UTF-8 text characters per token plus the full output cap.
        # It intentionally over-reserves rather than risk silently overrunning a free-tier quota.
        input_chars = sum(len(message.content) for message in messages)
        reserve = max(1, ceil(input_chars / 4)) + max(1, max_tokens)
        async with self._budget_lock:
            usage = self._job_usage.setdefault(
                budget_key,
                {
                    "calls": 0,
                    "reserved_tokens": 0,
                    "actual_tokens": 0,
                    "provider_attempts": {},
                },
            )
            calls = int(usage.get("calls") or 0)
            reserved = int(usage.get("reserved_tokens") or 0)
            if calls + 1 > self.settings.llm_max_calls_per_job:
                raise ProviderCallError(
                    "Per-job LLM call budget exhausted; deterministic research continues without "
                    "additional LLM enrichment",
                    retryable=False,
                )
            if reserved + reserve > self.settings.llm_max_reserved_tokens_per_job:
                raise ProviderCallError(
                    "Per-job LLM token budget exhausted; deterministic research continues without "
                    "additional LLM enrichment",
                    retryable=False,
                )

            attempts = usage.get("provider_attempts")
            if not isinstance(attempts, dict):
                attempts = {}
                usage["provider_attempts"] = attempts
            attempts[provider] = int(attempts.get(provider) or 0) + 1
            usage["calls"] = calls + 1
            usage["reserved_tokens"] = reserved + reserve

    async def _record_actual_usage(self, budget_key: str, result: ChatResult) -> None:
        actual = int(result.input_tokens or 0) + int(result.output_tokens or 0)
        if actual <= 0:
            return
        async with self._budget_lock:
            usage = self._job_usage.get(budget_key)
            if usage is not None:
                usage["actual_tokens"] = int(usage.get("actual_tokens") or 0) + actual

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
