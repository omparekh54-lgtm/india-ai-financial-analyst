from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

from app.auth import AuthenticatedUser, require_authenticated_user
from app.core.config import get_settings
from app.db import create_database_engine, database_health
from app.orchestration.plan import AnalysisMode, build_research_plan
from app.providers.router import Capability, ProviderRouter
from app.repositories.research import ResearchRepository
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
    version="0.4.0",
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
    """Public research request. Evidence/context injection is intentionally not accepted."""


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
        "auth_configured": bool(settings.supabase_url and settings.supabase_publishable_key),
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


@app.get("/v1/auth/me")
async def auth_me(
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict[str, object]:
    return {"id": str(user.id), "email": user.email}


@app.post("/v1/research/plan")
async def research_plan(request: ResearchPlanRequest) -> dict[str, object]:
    """Build the deterministic execution plan without calling any external API."""
    plan = build_research_plan(request.mode)
    return {
        "query": request.query,
        "mode": plan.mode,
        "stages": [stage.model_dump(mode="json") for stage in plan.stages],
    }


@app.get("/v1/research/jobs")
async def research_jobs(
    limit: int = Query(default=25, ge=1, le=100),
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    repository = ResearchRepository(create_database_engine(settings.database_url))
    jobs = await repository.list_user_jobs(user.id, limit=limit)
    return {"count": len(jobs), "jobs": jobs}


@app.get("/v1/research/jobs/{job_id}")
async def research_job(
    job_id: UUID,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    repository = ResearchRepository(create_database_engine(settings.database_url))
    job = await repository.get_user_job(user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Research job not found")
    return job


@app.post("/v1/research/run")
async def run_research(
    request: ResearchRunRequest,
    user: AuthenticatedUser = Depends(require_authenticated_user),
) -> dict[str, object]:
    """Execute the authenticated DB-backed 16-role research pipeline."""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")

    engine = create_database_engine(settings.database_url)
    service = ResearchService(engine, max_concurrency=settings.max_agent_concurrency)
    try:
        execution = await service.execute(
            query=request.query,
            mode=request.mode,
            requested_by=user.id,
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
