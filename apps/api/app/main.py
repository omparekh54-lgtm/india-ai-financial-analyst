from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import sentry_sdk
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.db import create_database_engine, database_health
from app.orchestration.plan import AnalysisMode, build_research_plan
from app.providers.router import Capability, ProviderRouter
from app.research.service import ResearchService

settings = get_settings()

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    if settings.database_url:
        await create_database_engine(settings.database_url).dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


class ResearchPlanRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    mode: AnalysisMode = AnalysisMode.FULL


class ResearchRunRequest(ResearchPlanRequest):
    context: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
async def health() -> dict[str, object]:
    db_ok = None
    if settings.database_url:
        db_ok = await database_health(create_database_engine(settings.database_url))
    return {
        "status": "ok" if db_ok is not False else "degraded",
        "service": settings.app_name,
        "environment": settings.app_env,
        "time": datetime.now(UTC).isoformat(),
        "database_configured": bool(settings.database_url),
        "database_healthy": db_ok,
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


@app.post("/v1/research/run")
async def run_research(request: ResearchRunRequest) -> dict[str, object]:
    """Execute the DB-backed 16-role research pipeline.

    Provider/network access remains governed by runtime kill switches. Request context is
    intended for trusted internal/testing use until authentication and per-user authorization
    are added.
    """
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")

    engine = create_database_engine(settings.database_url)
    service = ResearchService(engine, max_concurrency=settings.max_agent_concurrency)
    try:
        execution = await service.execute(
            query=request.query,
            mode=request.mode,
            context=request.context,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Research execution failed: {type(exc).__name__}",
        ) from exc

    return {
        "job_id": str(execution.job_id),
        "security_id": str(execution.security_id) if execution.security_id else None,
        "report": execution.report,
        "agents": [
            {
                "agent": output.agent.value,
                "ok": output.ok,
                "claim_count": len(output.claims),
                "evidence_count": len(output.evidence),
                "warnings": output.warnings,
                "errors": output.errors,
            }
            for output in execution.outputs
        ],
    }
