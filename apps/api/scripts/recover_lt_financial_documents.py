"""Recover LT's official NSE XBRL through curl using the existing financial importer.

Scope is deliberately pinned to the verified LT ISIN. No alternative data provider,
manual fact values, relaxed readiness contract, shell interpolation or paid service.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

import httpx
from lxml import etree
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.connectors.http_fetcher import SourceFetchError
from app.connectors.nse_financial_results import (
    NseFinancialResultRecord,
    NseFinancialResultsFetcher,
    normalize_xbrl_url,
)
from app.connectors.nse_xbrl import MAX_XBRL_BYTES, NseFinancialXbrlFetcher, validate_xbrl_payload
from app.core.config import get_settings
from app.core.financial_history_coverage import load_financial_history_coverage
from app.core.prepared_security_readiness import refresh_prepared_security_readiness
from app.db import create_database_engine
from app.ingestion.nse_financial_corpus import select_financial_result_records

if __package__:
    from scripts.backfill_nse_financial_results import _process_target, _target_for_identifier
else:
    from backfill_nse_financial_results import _process_target, _target_for_identifier

LT_ISIN = "INE018A01030"


class SingleRecordIndex(NseFinancialResultsFetcher):
    """Pass one already validated index record to the unchanged importer."""

    def __init__(self, record: NseFinancialResultRecord) -> None:
        super().__init__()
        self.record = record

    async def fetch_history(self, symbol: str) -> list[NseFinancialResultRecord]:
        if symbol != "LT" or self.record.symbol != "LT":
            raise SourceFetchError("Recovery index does not match LT")
        return [self.record]


async def recover_selected_documents(*, engine, target, selected, stored_urls,
                                     documents, dry_run):
    results = []
    for item in selected:
        url = item.record.xbrl_url
        if url in stored_urls:
            results.append({"source_uri": url, "status": "already_stored"})
            continue
        try:
            result = await _process_target(
                engine=engine, target=target,
                results_fetcher=SingleRecordIndex(item.record), xbrl_fetcher=documents,
                max_periods=1, min_selected_periods=0, document_delay_seconds=0,
                dry_run=dry_run, collect_available_history=True,
            )
            results.append(result)
        except (SourceFetchError, ValueError, httpx.HTTPError) as exc:
            failure = {"source_uri": url, "status": "failed",
                       "error_type": type(exc).__name__}
            if isinstance(exc, SourceFetchError):
                failure["source_error"] = str(exc)
            results.append(failure)
    return results


def validate_lt_issuer(content: bytes) -> None:
    root = etree.fromstring(content, etree.XMLParser(
        resolve_entities=False, no_network=True, recover=False, huge_tree=False,
    ))
    isins = {
        str(node.text or "").strip().upper()
        for node in root.xpath("//*[local-name()='ISIN' or local-name()='ISINNumber']")
    }
    if isins != {LT_ISIN}:
        raise SourceFetchError("Recovery document does not match the verified LT ISIN")


class CurlLtXbrlFetcher(NseFinancialXbrlFetcher):
    """Bounded public-archive transport; all normal payload/parser/ingestion guards remain."""

    async def start(self) -> None:
        # The public archive transport uses curl without session cookies. The base
        # fetcher warms the NSE website before its first request; that unrelated
        # website failure must not prevent a validated archive request.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=45.0, follow_redirects=False)

    async def _request(self, url: str) -> httpx.Response:
        normalized = normalize_xbrl_url(url)
        if normalized is None:
            raise SourceFetchError("Recovery requires an official NSE XBRL URL")
        with tempfile.TemporaryDirectory(prefix="lt-xbrl-recovery-") as directory:
            path = Path(directory) / "document.xml"
            # No redirect following: an archive redirect cannot reach a different host.
            # argv is passed without a shell, and no credentials/cookies are included.
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "curl", "--fail", "--silent", "--show-error",
                    "--connect-timeout", "10", "--max-time", "45",
                    "--retry", "2", "--retry-delay", "2", "--retry-max-time", "120",
                    "--max-filesize", str(MAX_XBRL_BYTES), "--proto", "=https",
                    "--user-agent", "Mozilla/5.0 IndiaAIFinancialAnalyst/0.7",
                    "--output", str(path), "--write-out", "%{http_code} %{content_type}",
                    normalized,
                ],
                capture_output=True, check=False, timeout=150,
            )
            status, _, declared = result.stdout.decode("utf-8", errors="replace").partition(" ")
            safe_status = status if len(status) == 3 and status.isdigit() else "unknown"
            if result.returncode != 0 or not path.exists():
                raise SourceFetchError(
                    "Bounded NSE archive recovery request failed "
                    f"(curl_exit={result.returncode}, http_status={safe_status})"
                )
            if path.stat().st_size > MAX_XBRL_BYTES:
                raise SourceFetchError("Recovery document exceeds the existing size limit")
            content = path.read_bytes()
            media_type = validate_xbrl_payload(
                content, content_type=declared.strip(), source_url=normalized,
            )
            if media_type not in {"application/xml", "text/xml", "application/xbrl+xml"}:
                raise SourceFetchError("LT recovery requires a validated XML instance")
            validate_lt_issuer(content)
            return httpx.Response(
                200, content=content, headers={"content-type": media_type},
                request=httpx.Request("GET", normalized),
            )


async def recover(*, dry_run: bool, refresh_readiness_only: bool) -> int:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL must be configured")
    engine = create_database_engine(settings.database_url)
    try:
        target = await _target_for_identifier(engine, "LT")
        async with engine.connect() as connection:
            isin = await connection.scalar(
                text("select isin from securities where id = :id"), {"id": target.security_id},
            )
        if isin != LT_ISIN:
            raise SourceFetchError("Canonical LT mapping differs from the reviewed ISIN")
        history = await load_financial_history_coverage(engine, security_id=target.security_id)
        if refresh_readiness_only:
            readiness = await refresh_prepared_security_readiness(
                engine, target.security_id, settings,
            )
            print(json.dumps({
                "symbol": "LT", "financial_history_complete": history.complete,
                "history": history.as_dict(), "readiness": readiness.as_dict(),
            }, sort_keys=True))
            return int(not history.complete)
        if history.complete:
            print(json.dumps({"symbol": "LT", "status": "already_complete"}, sort_keys=True))
            return 0
        async with engine.connect() as connection:
            stored_urls = set((await connection.execute(text("""
                select source_uri from sources s where s.security_id = :id
                and s.source_type = 'exchange_filing'
                and exists (select 1 from financial_facts f where f.source_id = s.id)
                and exists (select 1 from evidence_chunks ec where ec.source_id = s.id)
            """), {"id": target.security_id})).scalars().all())
        async with NseFinancialResultsFetcher() as index, CurlLtXbrlFetcher() as documents:
            records = await index.fetch_history("LT")
            selected = select_financial_result_records(records, max_periods=10)
            if not selected:
                raise SourceFetchError("No verified LT index records are available")
            results = await recover_selected_documents(
                engine=engine, target=target, selected=selected, stored_urls=stored_urls,
                documents=documents, dry_run=dry_run,
            )
        failures = sum(item["status"] == "failed" for item in results)
        print(json.dumps({"symbol": "LT", "results": results,
                          "failure_count": failures,
                          "status": "dry_run" if dry_run else "completed_with_gaps"
                          if failures else "completed"}, sort_keys=True, default=str))
        return int(failures > 0)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--refresh-readiness-only", action="store_true")
    args = parser.parse_args()
    try:
        return asyncio.run(recover(
            dry_run=args.dry_run, refresh_readiness_only=args.refresh_readiness_only,
        ))
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError,
            httpx.HTTPError, SQLAlchemyError, etree.XMLSyntaxError) as exc:
        # Diagnostics never print connection tracebacks, credential values or source text.
        diagnostic = {"symbol": "LT", "status": "failed",
                      "error_type": type(exc).__name__}
        if isinstance(exc, SourceFetchError):
            diagnostic["source_error"] = str(exc)
        print(json.dumps(diagnostic, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
