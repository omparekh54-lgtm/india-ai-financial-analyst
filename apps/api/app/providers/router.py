from dataclasses import dataclass
from enum import StrEnum

from app.core.config import Settings


class Capability(StrEnum):
    FAST_REASONING = "fast_reasoning"
    DEEP_REASONING = "deep_reasoning"
    MULTIMODAL = "multimodal"
    LONG_CONTEXT = "long_context"


@dataclass(frozen=True)
class ProviderChoice:
    provider: str
    capability: Capability
    reason: str


class ProviderRouter:
    """Deterministic provider routing with ordered fallbacks and no secret exposure."""

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
        else:
            ordered = [
                ("groq", bool(self.settings.groq_api_key), "Fast-reasoning primary"),
                ("cerebras", bool(self.settings.cerebras_api_key), "Low-latency fallback"),
                ("gemini", bool(self.settings.gemini_api_key), "General fallback"),
                ("nvidia", bool(self.settings.nvidia_api_key), "Deep-reasoning fallback"),
            ]

        choices = [
            ProviderChoice(provider, capability, reason)
            for provider, configured, reason in ordered
            if configured
        ]
        if choices:
            return choices

        return [ProviderChoice("unavailable", capability, "No configured provider credential")]

    def choose(self, capability: Capability) -> ProviderChoice:
        return self.candidates(capability)[0]
