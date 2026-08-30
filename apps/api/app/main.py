import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth import AuthenticatedUser, require_authenticated_user
from app.brokers.repository import BrokerRepository
from app.brokers.upstox_oauth import UpstoxOAuthError, UpstoxOAuthService
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.core.readiness import assert_production_ready, audit_settings
from app.db import create_database_engine, database_health
from app.orchestration.plan import AnalysisMode, build_research_plan
from app.providers.router import Capability, ProviderRouter
from app.repositories.research import ResearchRepository
from app.research.export import render_research_markdown, research_export_payload
from app.research.service import ResearchService

settings = get_settings()
CurrentUser = Annotated[AuthenticatedUser, Depends(require_authenticated_user)]

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    assert_production_ready(settings)
    yield
    if settings.database_url:
        await create_database_engine(settings.database_url).dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.6.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


class ResearchPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
        "upstox_oauth_configured": bool(
            settings.upstox_client_id
            and settings.upstox_client_secret
            and settings.upstox_redirect_uri
            and settings.broker_token_encryption_key
        ),
        "live_market_enabled": settings.enable_live_market,
        "external_llm_calls_enabled": settings.enable_external_llm_calls,
        "external_data_calls_enabled": settings.enable_external_data_calls,
    }


@app.get("/ready", response_class=ORJSONResponse)
async def readiness() -> ORJSONResponse:
    config_report = audit_settings(settings)
    db_ok = None
    if settings.database_url:
        db_ok = await database_health(create_database_engine(settings.database_url))
    ready = config_report.ready and db_ok is not False
    return ORJSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "service": settings.app_name,
            "environment": settings.app_env,
            "time": datetime.now(UTC).isoformat(),
            "database_healthy": db_ok,
            **config_report.as_dict(),
        },
    )


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


@app.get("/v1/system/data-readiness")
async def data_readiness(_user: CurrentUser) -> dict[str, object]:
    """Report corpus coverage separately from service/deployment readiness."""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    try:
        coverage = await load_data_coverage(engine)
    finally:
        await engine.dispose()
    return evaluate_data_coverage(coverage).as_dict()


@app.get("/v1/auth/me")
async def auth_me(user: CurrentUser) -> dict[str, object]:
    return {"id": str(user.id), "email": user.email}


@app.get("/v1/brokers")
async def broker_status(user: CurrentUser) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    service = UpstoxOAuthService(BrokerRepository(engine), settings)
    return {
        "live_market_enabled": settings.enable_live_market,
        "connections": [await service.status(user.id)],
    }


@app.post("/v1/brokers/upstox/connect")
async def connect_upstox(user: CurrentUser) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    service = UpstoxOAuthService(BrokerRepository(engine), settings)
    try:
        authorize_url = await service.begin(user.id)
    except UpstoxOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"provider": "upstox", "authorize_url": authorize_url}


@app.get("/v1/brokers/upstox/callback")
async def upstox_callback(code: str, state: str) -> RedirectResponse:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    service = UpstoxOAuthService(BrokerRepository(engine), settings)
    status = "connected"
    try:
        await service.complete(code=code, state=state)
    except UpstoxOAuthError:
        status = "error"
    target = f"{settings.web_app_url.rstrip('/')}?{urlencode({'broker': 'upstox', 'status': status})}"
    return RedirectResponse(target, status_code=303)


@app.delete("/v1/brokers/upstox")
async def disconnect_upstox(user: CurrentUser) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    service = UpstoxOAuthService(BrokerRepository(engine), settings)
    disconnected = await service.disconnect(user.id)
    return {"provider": "upstox", "disconnected": disconnected}


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
    user: CurrentUser,
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    repository = ResearchRepository(create_database_engine(settings.database_url))
    jobs = await repository.list_user_jobs(user.id, limit=limit)
    return {"count": len(jobs), "jobs": jobs}


@app.get("/v1/research/jobs/{job_id}")
async def research_job(
    job_id: UUID,
    user: CurrentUser,
) -> dict[str, object]:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    repository = ResearchRepository(create_database_engine(settings.database_url))
    job = await repository.get_user_job(user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Research job not found")
    return job


@app.get("/v1/research/jobs/{job_id}/export")
async def research_job_export(
    job_id: UUID,
    user: CurrentUser,
    export_format: str = Query(default="markdown", alias="format", pattern="^(markdown|json)$"),
) -> Response:
    """Download only the authenticated user's persisted report; no new research is generated."""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    repository = ResearchRepository(create_database_engine(settings.database_url))
    job = await repository.get_user_job(user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Research job not found")
    if not isinstance(job.get("report_json"), dict):
        raise HTTPException(status_code=409, detail="Research report is not completed")

    stem = f"india-equity-research-{job_id}"
    headers = {"Content-Disposition": f'attachment; filename="{stem}"'}
    if export_format == "json":
        headers["Content-Disposition"] = f'attachment; filename="{stem}.json"'
        return Response(
            content=json.dumps(
                research_export_payload(job),
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            media_type="application/json",
            headers=headers,
        )

    headers["Content-Disposition"] = f'attachment; filename="{stem}.md"'
    return Response(
        content=render_research_markdown(job),
        media_type="text/markdown",
        headers=headers,
    )


@app.post("/v1/research/run")
async def run_research(
    request: ResearchRunRequest,
    user: CurrentUser,
) -> dict[str, object]:
    """Execute the authenticated DB-backed 16-role research pipeline."""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")

    engine = create_database_engine(settings.database_url)
    service = ResearchService(
        engine,
        max_concurrency=settings.max_agent_concurrency,
        settings=settings,
    )
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
