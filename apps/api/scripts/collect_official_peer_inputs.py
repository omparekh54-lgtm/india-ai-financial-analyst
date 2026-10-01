"""Import one genuine official EOD session and backfill metrics for the prepared universe.

No classification guesses, usage approval overrides, new providers or invented facts.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, date, datetime

import httpx
from sqlalchemy import text

from app.connectors.nse_sectoral_indices import NSE_NIFTY50_INDEX_CSV
from app.core.config import get_settings
from app.core.data_readiness import load_data_coverage
from app.core.prepared_security_readiness import persist_prepared_security_readiness
from app.core.security_readiness import evaluate_security_readiness, load_security_agent_coverage
from app.db import create_database_engine
from app.ingestion.derived_metric_ingestion import DerivedSecurityMetricIngestor
from app.ingestion.derived_metrics import derive_peer_metrics, partition_metric_bundle
from app.ingestion.market import MarketBarIngestor
from app.ingestion.nse_bhavcopy import (
    MAX_FILE_BYTES,
    bhavcopy_url,
    full_delivery_url,
    parse_bhavcopy,
    parse_full_delivery,
)
from app.ingestion.reference_provenance import upsert_reference_source

if __package__:
    from scripts.backfill_derived_security_metrics import _facts, _market
else:
    from backfill_derived_security_metrics import _facts, _market


async def read_official_archive(client: httpx.AsyncClient, url: str) -> bytes:
    async with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Official bhavcopy failed: http={response.status_code}")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("Official bhavcopy exceeds the archive size guard")
        return bytes(content)


async def download_official_archive(url: str) -> bytes:
    try:
        async with asyncio.timeout(75):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60, connect=10), follow_redirects=False,
            ) as client:
                return await read_official_archive(client, url)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise RuntimeError(f"Official bhavcopy transport failed: {type(exc).__name__}") from exc


async def collect(session: date, dry_run: bool, *, refresh_readiness: bool = True,
                  report: str = "udiff") -> int:
    settings = get_settings()
    if not settings.database_url or not settings.enable_external_data_calls:
        raise RuntimeError("Existing database and external-data configuration are required")
    if session >= datetime.now(UTC).date() or (datetime.now(UTC).date() - session).days > 7:
        raise ValueError("Choose a completed EOD session within seven days")
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            targets = (await connection.execute(text("""
                select s.id, s.nse_symbol, s.isin from securities s
                where s.id in (select distinct security_id from security_agent_readiness_status)
                order by s.nse_symbol
            """))).mappings().all()
            db_size = await connection.scalar(text("select pg_database_size(current_database())"))
        if not targets or len(targets) > 50:
            raise ValueError("Prepared-universe import requires between one and fifty targets")
        if db_size >= 450_000_000:
            raise RuntimeError("Existing free-tier storage guard reached")
        if report not in {"udiff", "full-delivery"}:
            raise ValueError("Unknown official EOD report")
        url = full_delivery_url(session) if report == "full-delivery" else bhavcopy_url(session)
        content = await download_official_archive(url)
        target_identities = {
            str(row["nse_symbol"]): str(row["isin"]) for row in targets
        }
        identity_content = None
        if report == "full-delivery":
            identity_content = await download_official_archive(NSE_NIFTY50_INDEX_CSV)
            bars = parse_full_delivery(content, session=session, targets=target_identities,
                                       identity_content=identity_content)
        else:
            bars = parse_bhavcopy(content, session=session, targets=target_identities)
        checksum = hashlib.sha256(content).hexdigest()
        print(json.dumps({"event": "validated_official_eod", "session": session.isoformat(),
                          "source_uri": url, "sha256": checksum, "securities": len(bars),
                          "dry_run": dry_run}), flush=True)
        if dry_run:
            return 0
        market_ingestor = MarketBarIngestor(engine)
        metric_ingestor = DerivedSecurityMetricIngestor(engine)
        for target in targets:
            symbol, security_id = str(target["nse_symbol"]), target["id"]
            identity_metadata: dict[str, object] = {}
            if identity_content is not None:
                identity_metadata = {
                    "identity_source_uri": NSE_NIFTY50_INDEX_CSV,
                    "identity_source_sha256": hashlib.sha256(identity_content).hexdigest(),
                    "identity_retrieved_at": datetime.now(UTC).isoformat(),
                    "identity_match": "official_constituent_symbol_and_isin",
                }
            source_id = await upsert_reference_source(
                engine, security_id=security_id, source_type="reference_market_data",
                source_uri=url, title=f"NSE {report} EQ EOD — {symbol}",
                published_at=None, checksum=checksum, approval_reference=None,
                metadata={"importer": "collect_official_peer_inputs", "symbol": symbol,
                          "isin": str(target["isin"]), "session": session.isoformat(),
                          "provider": "nse", "is_adjusted": False, "row_count": 1,
                          "official_report": report, **identity_metadata},
            )
            await market_ingestor.ingest_security_bars(
                security_id=security_id, source_id=source_id, bars=[bars[symbol]],
            )
            facts, market = await _facts(engine, security_id), await _market(engine, security_id)
            bundle = derive_peer_metrics(facts, market=market)
            for partition in partition_metric_bundle(bundle):
                await metric_ingestor.ingest(
                    security_id=security_id, symbol=symbol, bundle=partition,
                )
            print(json.dumps({"event": "metrics_imported", "symbol": symbol,
                              "metrics": [m.metric_name for m in bundle.metrics],
                              "comparable_count": bundle.industry_comparable_count}), flush=True)
        if refresh_readiness:
            corpus = await load_data_coverage(engine)
            for target in targets:
                coverage, symbol = await load_security_agent_coverage(engine, target["id"])
                readiness = evaluate_security_readiness(
                    target["id"], symbol, coverage, corpus, settings, as_of=datetime.now(UTC),
                )
                await persist_prepared_security_readiness(engine, readiness)
        print(json.dumps({"status": "completed", "securities": len(targets),
                          "classification_data_written": False}), flush=True)
        return 0
    finally:
        await engine.dispose()


async def collect_stored_metrics(dry_run: bool) -> int:
    """Complete independent calculations even if an official price download is unavailable."""
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("Existing database configuration is required")
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            targets = (await connection.execute(text("""
                select s.id, s.nse_symbol from securities s
                where s.id in (select distinct security_id from security_agent_readiness_status)
                order by s.nse_symbol
            """))).mappings().all()
            db_size = await connection.scalar(text("select pg_database_size(current_database())"))
        if not targets or len(targets) > 50 or db_size >= 450_000_000:
            raise RuntimeError("Prepared-universe or storage guard failed")
        ingestor = DerivedSecurityMetricIngestor(engine)
        for target in targets:
            facts = await _facts(engine, target["id"])
            market = await _market(engine, target["id"])
            bundle = derive_peer_metrics(facts, market=market)
            partitions = partition_metric_bundle(bundle)
            if not dry_run:
                for partition in partitions:
                    await ingestor.ingest(security_id=target["id"],
                                          symbol=target["nse_symbol"], bundle=partition)
            print(json.dumps({"symbol": target["nse_symbol"], "dry_run": dry_run,
                              "metrics": [m.metric_name for m in bundle.metrics],
                              "partitions": len(partitions),
                              "industry_comparable_count": bundle.industry_comparable_count}),
                  flush=True)
        if not dry_run:
            corpus = await load_data_coverage(engine)
            for target in targets:
                coverage, symbol = await load_security_agent_coverage(engine, target["id"])
                readiness = evaluate_security_readiness(
                    target["id"], symbol, coverage, corpus, settings, as_of=datetime.now(UTC),
                )
                await persist_prepared_security_readiness(engine, readiness)
        print(json.dumps({"status": "dry_run" if dry_run else "completed",
                          "securities": len(targets), "source_permission_overrides": False}),
              flush=True)
        return 0
    finally:
        await engine.dispose()


async def refresh_prepared_readiness() -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("Existing database configuration is required")
    engine = create_database_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            targets = list((await connection.execute(text(
                "select distinct security_id from security_agent_readiness_status"
            ))).scalars())
        if not 1 <= len(targets) <= 50:
            raise RuntimeError("Prepared-universe refresh guard failed")
        corpus = await load_data_coverage(engine)
        for security_id in targets:
            coverage, symbol = await load_security_agent_coverage(engine, security_id)
            readiness = evaluate_security_readiness(
                security_id, symbol, coverage, corpus, settings, as_of=datetime.now(UTC),
            )
            await persist_prepared_security_readiness(engine, readiness)
        print(json.dumps({"status": "readiness_refreshed", "securities": len(targets)}), flush=True)
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat)
    parser.add_argument("--report", choices=("udiff", "full-delivery"), default="udiff")
    parser.add_argument("--stored-metrics-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-readiness-refresh", action="store_true",
                        help="Import only; refresh readiness separately in the configured worker")
    parser.add_argument("--refresh-readiness-only", action="store_true")
    args = parser.parse_args()
    if args.refresh_readiness_only:
        if (args.session or args.stored_metrics_only or args.dry_run
                or args.skip_readiness_refresh or args.report != "udiff"):
            parser.error("Readiness-only refresh cannot use import options")
        return asyncio.run(refresh_prepared_readiness())
    if args.stored_metrics_only:
        if args.session or args.skip_readiness_refresh or args.report != "udiff":
            parser.error("Stored-metrics mode cannot use session or skip-readiness options")
        return asyncio.run(collect_stored_metrics(args.dry_run))
    if not args.session:
        parser.error("--session is required for official EOD input collection")
    return asyncio.run(collect(args.session, args.dry_run,
                               refresh_readiness=not args.skip_readiness_refresh,
                               report=args.report))


if __name__ == "__main__":
    raise SystemExit(main())
