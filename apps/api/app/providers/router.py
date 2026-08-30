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
    """Selects an available provider without embedding credentials in code.

    Actual provider clients are added behind this interface. Routing is deterministic first;
    quota/latency/health-aware scoring will be layered on once telemetry is connected.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def choose(self, capability: Capability) -> ProviderChoice:
        if not self.settings.enable_external_llm_calls:
            return ProviderChoice("disabled", capability, "External LLM calls disabled by runtime flag")

        if capability in {Capability.MULTIMODAL, Capability.LONG_CONTEXT} and self.settings.gemini_api_key:
            return ProviderChoice("gemini", capability, "Preferred multimodal/long-context provider")

        if capability == Capability.DEEP_REASONING and self.settings.nvidia_api_key:
            return ProviderChoice("nvidia", capability, "Preferred deep-reasoning provider")

        if self.settings.groq_api_key:
            return ProviderChoice("groq", capability, "Primary fast reasoning provider")

        if self.settings.cerebras_api_key:
            return ProviderChoice("cerebras", capability, "Low-latency fallback provider")

        if self.settings.gemini_api_key:
            return ProviderChoice("gemini", capability, "Available fallback provider")

        if self.settings.nvidia_api_key:
            return ProviderChoice("nvidia", capability, "Available fallback provider")

        return ProviderChoice("unavailable", capability, "No configured provider credential")
