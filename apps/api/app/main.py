from contextlib import asynccontextmanager
from datetime import UTC, datetime

import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from app.core.config import get_settings
from app.providers.router import Capability, ProviderRouter

settings = get_settings()

if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.app_env, traces_sample_rate=0.1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Future: initialize DB pool, provider health registry, market stream consumers.
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
        "time": datetime.now(UTC).isoformat(),
        "live_market_enabled": settings.enable_live_market,
        "external_llm_calls_enabled": settings.enable_external_llm_calls,
    }


@app.get("/v1/system/provider-routing")
async def provider_routing() -> dict[str, object]:
    router = ProviderRouter(settings)
    return {
        capability.value: router.choose(capability).__dict__
        for capability in Capability
    }


@app.get("/v1/system/agents")
async def agents() -> dict[str, object]:
    from app.agents.contracts import AgentName

    return {"count": len(AgentName), "agents": [agent.value for agent in AgentName]}
