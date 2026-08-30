from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "India AI Financial Analyst"
    log_level: str = "INFO"

    database_url: str | None = None
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    supabase_service_role_key: str | None = None

    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    nvidia_api_key: str | None = None
    cerebras_api_key: str | None = None

    groq_base_url: str = "https://api.groq.com/openai/v1"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    groq_model: str = "openai/gpt-oss-20b"
    nvidia_model: str = "deepseek-ai/deepseek-v4-pro-0813"
    cerebras_model: str = "gpt-oss-120b"
    gemini_model: str = "gemini-2.5-flash"

    tavily_api_key: str | None = None
    fred_api_key: str | None = None
    alpha_vantage_api_key: str | None = None

    tavily_base_url: str = "https://api.tavily.com"
    fred_base_url: str = "https://api.stlouisfed.org/fred"
    alpha_vantage_base_url: str = "https://www.alphavantage.co/query"

    fyers_client_id: str | None = None
    fyers_secret_key: str | None = None
    fyers_redirect_uri: str | None = None
    angel_api_key: str | None = None
    angel_client_code: str | None = None
    angel_totp_secret: str | None = None
    upstox_client_id: str | None = None
    upstox_client_secret: str | None = None
    upstox_redirect_uri: str | None = None

    sentry_dsn: str | None = None
    posthog_key: str | None = None
    posthog_host: str = "https://us.i.posthog.com"

    enable_live_market: bool = False
    enable_external_llm_calls: bool = False
    enable_external_data_calls: bool = False
    max_agent_concurrency: int = Field(default=6, ge=1, le=16)
    max_research_job_seconds: int = Field(default=240, ge=30, le=1800)


@lru_cache
def get_settings() -> Settings:
    return Settings()
