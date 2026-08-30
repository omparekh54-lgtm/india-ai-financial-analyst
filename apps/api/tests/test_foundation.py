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
