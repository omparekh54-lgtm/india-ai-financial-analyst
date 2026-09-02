import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

import sentry_sdk
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth import AuthenticatedUser, require_authenticated_user
from app.brokers.repository import BrokerRepository
from app.brokers.upstox_oauth import UpstoxOAuthError, UpstoxOAuthService
from app.calibration_api import router as calibration_router
from app.comparison_api import router as comparison_router
from app.core.agent_data_readiness import evaluate_agent_readiness, load_agent_data_coverage
from app.core.config import get_settings
from app.core.data_readiness import evaluate_data_coverage, load_data_coverage
from app.core.readiness import assert_production_ready, audit_settings
from app.core.research_gate import ResearchCorpusNotReadyError, enforce_research_corpus_ready
from app.core.usage import ResearchUsageLimitError
from app.db import create_database_engine, database_health
from app.monitoring_api import router as monitoring_router
from app.orchestration.plan import AnalysisMode, ResearchDepth, build_research_plan
from app.portfolio_api import router as portfolio_router
from app.providers.router import Capability, ProviderRouter
from app.repositories.research import ResearchRepository
from app.research.export import render_research_markdown, research_export_payload
from app.research.service import ResearchService
from app.usage_api import router as usage_router
from app.watchlists_api import router as watchlists_router

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
    version="0.10.0",
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
app.include_router(watchlists_router)
app.include_router(monitoring_router)
app.include_router(portfolio_router)
app.include_router(comparison_router)
app.include_router(calibration_router)
app.include_router(usage_router)


@app.exception_handler(ResearchUsageLimitError)
async def research_usage_limit_handler(
    _request: Request,
    exc: ResearchUsageLimitError,
) -> ORJSONResponse:
    return ORJSONResponse(
        status_code=429,
        content={
            "detail": {
                "code": exc.code,
                "message": "Daily research usage limit reached.",
                "used": exc.used,
                "limit": exc.limit,
            }
        },
    )


class ResearchPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    mode: AnalysisMode = AnalysisMode.FULL
    depth: ResearchDepth = ResearchDepth.STANDARD


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
        "free_only": settings.free_only,
        "usage_limits_enabled": settings.enable_usage_limits,
        "commercial_launch_enabled": settings.commercial_launch_enabled,
        "research_queue": "postgres_worker",
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

    return {
        "count": len(AgentName),
        "agents": [agent.value for agent in AgentName],
        "analysis_modes": [mode.value for mode in AnalysisMode],
        "research_depths": [depth.value for depth in ResearchDepth],
    }


@app.get("/v1/system/data-readiness")
async def data_readiness(_user: CurrentUser) -> dict[str, object]:
    """Report global corpus coverage and every agent's real-data readiness contract."""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    try:
        coverage = await load_data_coverage(engine)
        agent_coverage = await load_agent_data_coverage(engine)
    finally:
        await engine.dispose()

    corpus_report = evaluate_data_coverage(coverage)
    agent_report = evaluate_agent_readiness(agent_coverage, coverage, settings)
    payload = corpus_report.as_dict()
    payload["agent_readiness"] = agent_report.as_dict()
    payload["blocking_agents"] = list(agent_report.blocking_agents)
    payload["ready"] = corpus_report.ready and agent_report.ready
    return payload


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
    plan = build_research_plan(request.mode, request.depth)
    return {
        "query": request.query,
        "mode": plan.mode,
        "depth": plan.depth,
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


@app.get("/v1/research/jobs/{job_id}/evidence")
async def research_job_evidence(
    job_id: UUID,
    user: CurrentUser,
) -> dict[str, object]:
    """Return the authenticated user's claim-to-source evidence graph for one job."""
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    repository = ResearchRepository(create_database_engine(settings.database_url))
    job = await repository.get_user_job(user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Research job not found")
    claims = await repository.get_user_job_evidence(user.id, job_id)
    linked_evidence_count = 0
    for claim in claims:
        evidence = claim.get("evidence")
        if isinstance(evidence, list):
            linked_evidence_count += len(evidence)
    return {
        "job_id": str(job_id),
        "claim_count": len(claims),
        "linked_evidence_count": linked_evidence_count,
        "claims": claims,
    }


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


async def _enforce_research_ready_or_503() -> None:
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")
    engine = create_database_engine(settings.database_url)
    try:
        await enforce_research_corpus_ready(
            engine,
            app_env=settings.app_env,
            settings=settings,
        )
    except ResearchCorpusNotReadyError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "research_corpus_not_ready",
                "message": (
                    "Production research is blocked until the global corpus and all required "
                    "agent data-readiness gates pass."
                ),
                "blocking_agents": list(exc.blocking_agents),
                "errors": list(exc.errors[:12]),
            },
        ) from exc
    finally:
        await engine.dispose()


@app.post("/v1/research/enqueue", status_code=202)
async def enqueue_research(
    request: ResearchRunRequest,
    user: CurrentUser,
) -> dict[str, object]:
    """Create a durable job and return immediately; the research worker performs the analysis."""
    await _enforce_research_ready_or_503()
    assert settings.database_url is not None
    engine = create_database_engine(settings.database_url)
    service = ResearchService(
        engine,
        max_concurrency=settings.max_agent_concurrency,
        settings=settings,
    )
    try:
        job_id = await service.enqueue(
            query=request.query,
            mode=request.mode,
            depth=request.depth,
            requested_by=user.id,
        )
    finally:
        await engine.dispose()
    return {
        "job_id": str(job_id),
        "status": "queued",
        "depth": request.depth.value,
        "poll_path": f"/v1/research/jobs/{job_id}",
        "evidence_explorer_path": f"/v1/research/jobs/{job_id}/evidence",
    }


@app.post("/v1/research/run")
async def run_research(
    request: ResearchRunRequest,
    user: CurrentUser,
) -> dict[str, object]:
    """Compatibility path for immediate internal execution; product UI uses /enqueue."""
    await _enforce_research_ready_or_503()
    assert settings.database_url is not None
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
            depth=request.depth,
            requested_by=user.id,
        )
    except ResearchUsageLimitError:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Research execution failed: {type(exc).__name__}",
        ) from exc
    finally:
        await engine.dispose()

    return {
        "job_id": str(execution.job_id),
        "security_id": str(execution.security_id) if execution.security_id else None,
        "depth": execution.depth.value,
        "evidence_explorer_path": f"/v1/research/jobs/{execution.job_id}/evidence",
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
