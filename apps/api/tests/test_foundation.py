from app.agents.contracts import AgentName
from app.core.config import Settings
from app.providers.router import Capability, ProviderRouter


def test_sixteen_agent_contracts_are_defined() -> None:
    assert len(AgentName) == 16


def test_external_llm_calls_default_to_disabled() -> None:
    settings = Settings(_env_file=None)
    assert settings.enable_external_llm_calls is False
    choice = ProviderRouter(settings).choose(Capability.FAST_REASONING)
    assert choice.provider == "disabled"


def test_live_market_defaults_to_disabled() -> None:
    settings = Settings(_env_file=None)
    assert settings.enable_live_market is False


def test_free_only_defaults_to_enabled() -> None:
    settings = Settings(_env_file=None)
    assert settings.free_only is True


def test_free_only_blocks_paid_provider_routes() -> None:
    settings = Settings(
        _env_file=None,
        enable_external_llm_calls=True,
        groq_api_key="configured-for-test",
        groq_route_cost="paid",
        free_only=True,
    )
    choice = ProviderRouter(settings).choose(Capability.FAST_REASONING)
    assert choice.provider == "unavailable"
    assert "FREE_ONLY" in choice.reason


def test_paid_route_requires_explicit_free_only_disable() -> None:
    settings = Settings(
        _env_file=None,
        enable_external_llm_calls=True,
        groq_api_key="configured-for-test",
        groq_route_cost="paid",
        free_only=False,
    )
    choice = ProviderRouter(settings).choose(Capability.FAST_REASONING)
    assert choice.provider == "groq"
    assert choice.route_cost == "paid"
