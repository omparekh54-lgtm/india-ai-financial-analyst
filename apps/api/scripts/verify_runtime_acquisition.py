"""Verify existing Tavily access in its own runtime and refresh prepared coverage.

Two bounded basic searches; no credential export, new provider, research job,
generated news, source approval override, or source data writes. News remains
on-demand under the existing research acquisition contract.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.connectors.tavily import TavilyConnector
from app.core.config import get_settings
from app.core.data_readiness import load_data_coverage
from app.core.prepared_security_readiness import persist_prepared_security_readiness
from app.core.security_readiness import evaluate_security_readiness, load_security_agent_coverage
from app.db import create_database_engine


async def run(refresh_limit: int) -> int:
    settings = get_settings()
    if not settings.enable_external_data_calls or not settings.tavily_api_key:
        print(json.dumps({"status": "unconfigured", "external_data_enabled":
                          settings.enable_external_data_calls,
                          "tavily_configured": bool(settings.tavily_api_key)}))
        return 1
    connector = TavilyConnector(settings)
    for topic in ("news", "general"):
        envelopes = await connector.search(
            "Reliance Industries RELIANCE latest NSE BSE company announcement",
            topic=topic, max_results=3,
            include_domains=["nseindia.com", "bseindia.com", "sebi.gov.in"],
        )
        print(json.dumps({"status": "provider_verified", "topic": topic,
                          "source_count": len(envelopes), "search_depth": "basic",
                          "sources": [{"uri": item.source_uri,
                                       "published_at": item.published_at.isoformat()
                                       if item.published_at else None,
                                       "retrieved_at": item.retrieved_at.isoformat()}
                                      for item in envelopes]}, sort_keys=True))
    if refresh_limit == 0:
        return 0
    if not settings.database_url:
        raise RuntimeError("Database configuration is required for readiness refresh")
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            ids = (await connection.execute(text("""
                select distinct security_id from security_agent_readiness_status
                order by security_id limit :limit
            """), {"limit": refresh_limit})).scalars().all()
        corpus = await load_data_coverage(engine)
        for security_id in ids:
            coverage, symbol = await load_security_agent_coverage(engine, security_id)
            evaluated = datetime.now(UTC)
            readiness = evaluate_security_readiness(
                security_id, symbol, coverage, corpus, settings, as_of=evaluated,
            )
            await persist_prepared_security_readiness(
                engine, readiness, evaluated_at=evaluated,
            )
            print(json.dumps({"symbol": symbol, "ready": readiness.ready,
                              "blocking_agents": list(readiness.blocking_agents)},
                             sort_keys=True))
        print(json.dumps({"status": "completed", "refreshed": len(ids),
                          "news_collection_policy": "fresh_on_demand",
                          "source_data_written": False}))
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-limit", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.refresh_limit <= 50:
        parser.error("--refresh-limit must be between 0 and 50")
    try:
        return asyncio.run(run(args.refresh_limit))
    except (httpx.HTTPError, RuntimeError, ValueError, SQLAlchemyError) as exc:
        # Never print provider errors, SQL parameters, connection URLs, or keys.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
