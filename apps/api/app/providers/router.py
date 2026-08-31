from dataclasses import dataclass
from enum import StrEnum

from app.core.config import ProviderRouteCost, Settings


class Capability(StrEnum):
    FAST_REASONING = "fast_reasoning"
    LOW_LATENCY = "low_latency"
    DEEP_REASONING = "deep_reasoning"
    MULTIMODAL = "multimodal"
    LONG_CONTEXT = "long_context"


@dataclass(frozen=True)
class ProviderChoice:
    provider: str
    capability: Capability
    reason: str
    route_cost: ProviderRouteCost | None = None


class ProviderRouter:
    """Deterministic provider routing with a hard FREE_ONLY cost-policy gate."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def candidates(self, capability: Capability) -> list[ProviderChoice]:
        if not self.settings.enable_external_llm_calls:
            return [
                ProviderChoice(
                    "disabled",
                    capability,
                    "External LLM calls disabled by runtime flag",
                )
            ]

        ordered: list[tuple[str, bool, str]]
        if capability in {Capability.MULTIMODAL, Capability.LONG_CONTEXT}:
            ordered = [
                ("gemini", bool(self.settings.gemini_api_key), "Multimodal/long-context primary"),
                ("nvidia", bool(self.settings.nvidia_api_key), "Deep-reasoning fallback"),
                ("groq", bool(self.settings.groq_api_key), "Fast-reasoning fallback"),
                ("cerebras", bool(self.settings.cerebras_api_key), "Low-latency fallback"),
            ]
        elif capability == Capability.DEEP_REASONING:
            ordered = [
                ("nvidia", bool(self.settings.nvidia_api_key), "Deep-reasoning primary"),
                ("groq", bool(self.settings.groq_api_key), "General reasoning fallback"),
                ("cerebras", bool(self.settings.cerebras_api_key), "Low-latency fallback"),
                ("gemini", bool(self.settings.gemini_api_key), "General fallback"),
            ]
        elif capability == Capability.LOW_LATENCY:
            ordered = [
                ("cerebras", bool(self.settings.cerebras_api_key), "Low-latency primary"),
                ("groq", bool(self.settings.groq_api_key), "Fast-reasoning fallback"),
                ("gemini", bool(self.settings.gemini_api_key), "General fallback"),
                ("nvidia", bool(self.settings.nvidia_api_key), "Deep-reasoning fallback"),
            ]
        else:
            ordered = [
                ("groq", bool(self.settings.groq_api_key), "Fast-reasoning primary"),
                ("cerebras", bool(self.settings.cerebras_api_key), "Low-latency fallback"),
                ("gemini", bool(self.settings.gemini_api_key), "General fallback"),
                ("nvidia", bool(self.settings.nvidia_api_key), "Deep-reasoning fallback"),
            ]

        configured: list[ProviderChoice] = []
        blocked_paid_routes: list[str] = []
        for provider, has_credential, reason in ordered:
            if not has_credential:
                continue
            route_cost = self.settings.provider_route_cost(provider)
            if self.settings.free_only and route_cost != "free":
                blocked_paid_routes.append(provider)
                continue
            configured.append(
                ProviderChoice(
                    provider=provider,
                    capability=capability,
                    reason=reason,
                    route_cost=route_cost,
                )
            )

        if configured:
            return configured

        if blocked_paid_routes:
            return [
                ProviderChoice(
                    "unavailable",
                    capability,
                    "FREE_ONLY blocked configured paid routes: " + ", ".join(blocked_paid_routes),
                )
            ]

        return [ProviderChoice("unavailable", capability, "No configured provider credential")]

    def choose(self, capability: Capability) -> ProviderChoice:
        return self.candidates(capability)[0]
