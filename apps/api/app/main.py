from contextlib import asynccontextmanager
from datetime import UTC, datetime

import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.orchestration.plan import AnalysisMode, build_research_plan
from app.providers.router import Capability, ProviderRouter

settings = get_settings()

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Future: initialize DB pool, provider health registry, and market-stream consumers.
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)


class ResearchPlanRequest(BaseModel):
    query: str
    mode: AnalysisMode = AnalysisMode.FULL


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
        "time": datetime.now(UTC).isoformat(),
        "live_market_enabled": settings.enable_live_market,
        "external_llm_calls_enabled": settings.enable_external_llm_calls,
        "external_data_calls_enabled": settings.enable_external_data_calls,
    }


@app.get("/v1/system/provider-routing")
async def provider_routing() -> dict[str, object]:
    router = ProviderRouter(settings)
    return {
        capability.value: [choice.__dict__ for choice in router.candidates(capability)]
        for capability in Capability
    }


@app.get("/v1/system/agents")
async def agents() -> dict[str, object]:
    from app.agents.contracts import AgentName

    return {"count": len(AgentName), "agents": [agent.value for agent in AgentName]}


@app.post("/v1/research/plan")
async def research_plan(request: ResearchPlanRequest) -> dict[str, object]:
    """Build the deterministic execution plan without calling any external API."""
    plan = build_research_plan(request.mode)
    return {
        "query": request.query,
        "mode": plan.mode,
        "stages": [stage.model_dump(mode="json") for stage in plan.stages],
    }
