from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.providers.router import Capability, ProviderRouter


@dataclass(frozen=True)
class ProviderActivationReport:
    ready: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    integrations: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "integrations": list(self.integrations),
            "verification_level": "configuration_and_policy_only",
            "note": (
                "This gate proves activation policy and credential presence without exposing secrets. "
                "It does not claim that a third-party endpoint, quota, licence or live market session "
                "has been successfully exercised."
            ),
        }


def evaluate_provider_activation(settings: Settings) -> ProviderActivationReport:
    errors: list[str] = []
    warnings: list[str] = []
    integrations: list[dict[str, object]] = []

    if not settings.free_only:
        errors.append("FREE_ONLY must remain enabled for the configured free-first launch policy.")

    llm_router = ProviderRouter(settings)
    llm_capabilities = [Capability.FAST_REASONING, Capability.DEEP_REASONING]
    if settings.enable_multimodal_document_analysis:
        llm_capabilities.append(Capability.MULTIMODAL)
    if settings.enable_external_llm_calls:
        for capability in llm_capabilities:
            choices = llm_router.candidates(capability)
            eligible = [choice for choice in choices if choice.provider not in {"disabled", "unavailable"}]
            integrations.append(
                {
                    "integration": f"llm:{capability.value}",
                    "enabled": True,
                    "configured": bool(eligible),
                    "eligible_free_routes": [choice.provider for choice in eligible],
                }
            )
            if not eligible:
                errors.append(
                    f"No FREE_ONLY-compatible configured LLM route is available for {capability.value}."
                )
    else:
        integrations.append(
            {
                "integration": "llm",
                "enabled": False,
                "configured": False,
                "status": "disabled_by_operator",
            }
        )

    if settings.enable_external_data_calls:
        tavily_ready = bool(settings.tavily_api_key)
        integrations.append(
            {
                "integration": "tavily_web_research",
                "enabled": True,
                "configured": tavily_ready,
            }
        )
        if not tavily_ready:
            errors.append("External data calls are enabled but TAVILY_API_KEY is not configured.")
        integrations.append(
            {
                "integration": "fred_macro_enrichment",
                "enabled": bool(settings.fred_api_key),
                "configured": bool(settings.fred_api_key),
                "required": False,
            }
        )
    else:
        integrations.append(
            {
                "integration": "external_data",
                "enabled": False,
                "configured": False,
                "status": "disabled_by_operator",
            }
        )

    if settings.enable_live_market:
        upstox_ready = bool(
            settings.upstox_client_id
            and settings.upstox_client_secret
            and settings.upstox_redirect_uri
            and settings.broker_token_encryption_key
        )
        integrations.append(
            {
                "integration": "upstox_live_market",
                "enabled": True,
                "configured": upstox_ready,
            }
        )
        if not upstox_ready:
            errors.append(
                "Live market is enabled but Upstox OAuth credentials/redirect URI/token encryption "
                "key are incomplete."
            )
    else:
        integrations.append(
            {
                "integration": "upstox_live_market",
                "enabled": False,
                "configured": False,
                "status": "disabled_by_operator",
            }
        )

    if settings.enable_multimodal_document_analysis:
        multimodal_ready = bool(
            settings.enable_external_llm_calls
            and settings.gemini_api_key
            and (settings.gemini_multimodal_model or settings.gemini_model)
            and settings.provider_route_cost("gemini") == "free"
        )
        integrations.append(
            {
                "integration": "gemini_multimodal",
                "enabled": True,
                "configured": multimodal_ready,
            }
        )
        if not multimodal_ready:
            errors.append(
                "Multimodal analysis is enabled without a FREE_ONLY-compatible configured Gemini route."
            )

    if settings.enable_audio_transcription:
        audio_ready = bool(
            settings.enable_external_llm_calls
            and settings.gemini_api_key
            and settings.gemini_audio_model
            and settings.provider_route_cost("gemini") == "free"
        )
        integrations.append(
            {
                "integration": "gemini_audio",
                "enabled": True,
                "configured": audio_ready,
            }
        )
        if not audio_ready:
            errors.append(
                "Audio transcription is enabled without a FREE_ONLY-compatible configured Gemini route."
            )

    if settings.enable_event_research and not settings.enable_external_data_calls:
        warnings.append(
            "Event research is enabled while external data acquisition is disabled; event jobs may "
            "be limited to already-ingested official evidence."
        )

    return ProviderActivationReport(
        ready=not errors,
        errors=tuple(dict.fromkeys(errors)),
        warnings=tuple(dict.fromkeys(warnings)),
        integrations=tuple(integrations),
    )
