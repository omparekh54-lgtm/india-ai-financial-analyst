from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from uuid import UUID

from lxml import etree
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.http_fetcher import SourceFetchError
from app.connectors.nse_financial_results import NseFinancialResultsFetcher
from app.connectors.nse_xbrl import NseFinancialXbrlFetcher
from app.core.financial_history_policy import required_financial_periods
from app.core.market_history_coverage import parse_listing_date
from app.ingestion.exchange import ExchangeDisclosure, ExchangeDisclosureIngestor
from app.ingestion.financials import FinancialFactIngestor
from app.ingestion.nse_financial_corpus import (
    financial_result_headline,
    financial_result_metadata,
    select_financial_result_records,
)
from app.ingestion.xbrl_evidence import XbrlEvidenceIngestor
from app.ingestion.xbrl_financials import parse_financial_xbrl

# Matches the structural bar the batch sweep (backfill_nse_financial_results.py) uses for
# its own "financial_ready" target-selection filter: at least this many distinct reporting
# periods and canonical fact types already sourced is treated as "good enough" to skip a
# live fetch on this request.
#
# This is deliberately the *structural* bar, not the freshness-gated one in
# financial_history_policy.evaluate_financial_history (FINANCIAL_FRESHNESS_DAYS=200). NSE's
# public financial-results API currently returns data capped around the same calendar date
# for every company regardless of symbol or date-range params (confirmed 2026-09-20, even
# for large caps) -- gating a cache-hit on freshness here would mean this module re-fetches
# on every single research request forever, which defeats the entire point of caching.
# Revisit this constant if/when the NSE recency-cap issue is separately resolved.
MIN_CACHED_PERIODS = 8
MIN_CACHED_FACT_TYPES = 6

# Bounds the worst case: a hung connection or a pathologically slow NSE response should
# fail the on-demand fetch (and let the research job continue with whatever was already
# cached) rather than hang the calling research job indefinitely.
DEFAULT_TIMEOUT_SECONDS = 480.0

# Per-process guard against two concurrent research requests for the same never-before-seen
# security both paying for a live fetch at once. This does not protect against two
# different worker *processes* racing (that would need a DB-level advisory lock), but it
# closes the common case cheaply: two users opening the same brand-new ticker moments apart
# within the same running worker.
_locks: dict[UUID, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _lock_for(security_id: UUID) -> asyncio.Lock:
    async with _locks_guard:
        lock = _locks.get(security_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks[security_id] = lock
        return lock


async def ensure_financial_history(
    engine: AsyncEngine,
    security_id: UUID,
    *,
    max_periods: int = 10,
    min_selected_periods: int = 0,
    document_delay_seconds: float = 0.1,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Serve cached financial history for one security, or fetch it live on a cache miss.

    This is the on-demand counterpart to the batch sweep in
    scripts/backfill_nse_financial_results.py: instead of proactively working through the
    whole NSE equity universe, it only ever fetches a company the first time someone
    actually asks about it, then leaves it cached for every request after that.

    Called from the live research path, so this function must never raise -- a legitimate
    data gap or a transient NSE failure should degrade gracefully (the agents simply see
    whatever was already cached, which may be nothing, and must disclose that), not crash
    the user's request. Every branch returns a status dict instead.
    """
    cached = await _cached_coverage(engine, security_id)
    if cached is None:
        return {"status": "unknown_security", "security_id": str(security_id)}
    symbol, legal_name, listing_date, sourced_periods, sourced_fact_types = cached
    if not symbol:
        return {"status": "no_nse_symbol", "security_id": str(security_id)}
    if _is_cache_hit(sourced_periods, sourced_fact_types):
        return {
            "status": "cache_hit",
            "symbol": symbol,
            "sourced_periods": sourced_periods,
            "sourced_fact_types": sourced_fact_types,
        }

    lock = await _lock_for(security_id)
    async with lock:
        # Re-check after acquiring the lock: another concurrent request for this same
        # security may have already completed the fetch while we were waiting.
        cached = await _cached_coverage(engine, security_id)
        if cached is None:
            return {"status": "unknown_security", "security_id": str(security_id)}
        symbol, legal_name, listing_date, sourced_periods, sourced_fact_types = cached
        if _is_cache_hit(sourced_periods, sourced_fact_types):
            return {
                "status": "cache_hit",
                "symbol": symbol,
                "sourced_periods": sourced_periods,
                "sourced_fact_types": sourced_fact_types,
            }

        try:
            return await asyncio.wait_for(
                _fetch_and_ingest(
                    engine=engine,
                    security_id=security_id,
                    symbol=symbol,
                    legal_name=legal_name,
                    listing_date=listing_date,
                    max_periods=max_periods,
                    min_selected_periods=min_selected_periods,
                    document_delay_seconds=document_delay_seconds,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return {
                "status": "timed_out",
                "symbol": symbol,
                "timeout_seconds": timeout_seconds,
            }
        except (SourceFetchError, ValueError) as exc:
            # A genuine data gap (e.g. a recent listing without enough historical periods
            # yet) or an NSE fetch failure. Not fatal to the research request.
            return {
                "status": "unavailable",
                "symbol": symbol,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        except (TypeError, RuntimeError, etree.XMLSyntaxError) as exc:
            return {
                "status": "error",
                "symbol": symbol,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }


def _is_cache_hit(sourced_periods: int, sourced_fact_types: int) -> bool:
    return sourced_periods >= MIN_CACHED_PERIODS and sourced_fact_types >= MIN_CACHED_FACT_TYPES


async def _cached_coverage(
    engine: AsyncEngine,
    security_id: UUID,
) -> tuple[str, str, date | None, int, int] | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    select
                      s.nse_symbol,
                      s.legal_name,
                      s.metadata->>'date_of_listing' as date_of_listing,
                      count(distinct ff.period_end) filter (
                        where ff.source_id is not null
                      ) as sourced_periods,
                      count(distinct ff.fact_name) filter (
                        where ff.source_id is not null
                      ) as sourced_fact_types
                    from securities s
                    left join financial_facts ff on ff.security_id = s.id
                    where s.id = :security_id
                    group by s.id, s.nse_symbol, s.legal_name, s.metadata->>'date_of_listing'
                    """
                ),
                {"security_id": security_id},
            )
        ).mappings().one_or_none()
    if row is None:
        return None
    symbol = str(row.get("nse_symbol") or "").strip().upper()
    legal_name = str(row.get("legal_name") or symbol)
    listing_date = parse_listing_date(row.get("date_of_listing"))
    sourced_periods = int(row.get("sourced_periods") or 0)
    sourced_fact_types = int(row.get("sourced_fact_types") or 0)
    return symbol, legal_name, listing_date, sourced_periods, sourced_fact_types


async def _fetch_and_ingest(
    *,
    engine: AsyncEngine,
    security_id: UUID,
    symbol: str,
    legal_name: str,
    listing_date: date | None,
    max_periods: int,
    min_selected_periods: int,
    document_delay_seconds: float,
) -> dict[str, object]:
    async with (
        NseFinancialResultsFetcher() as results_fetcher,
        NseFinancialXbrlFetcher() as xbrl_fetcher,
    ):
        records = await results_fetcher.fetch_history(symbol)
        selected = select_financial_result_records(records, max_periods=max_periods)
        policy_required = required_financial_periods(listing_date, as_of=datetime.now(UTC).date())
        required_selected = max(policy_required, min_selected_periods)
        if len(selected) < required_selected:
            raise ValueError(
                f"{symbol} exposes only {len(selected)} distinct NSE XBRL result periods; "
                f"minimum required is {required_selected} "
                f"(listing-age policy requires {policy_required})"
            )

        event_ingestor = ExchangeDisclosureIngestor(engine)
        fact_ingestor = FinancialFactIngestor(engine)
        evidence_ingestor = XbrlEvidenceIngestor(engine)
        documents: list[dict[str, object]] = []

        for position, item in enumerate(selected):
            fetched = await xbrl_fetcher.fetch(item.record.xbrl_url)
            facts = parse_financial_xbrl(fetched.content, fetched.media_type)
            if not facts:
                raise ValueError(f"{symbol} XBRL produced no numeric facts: {fetched.source_url}")

            metadata = financial_result_metadata(item)
            disclosure = await event_ingestor.ingest(
                ExchangeDisclosure(
                    security_id=security_id,
                    exchange="NSE",
                    source_uri=fetched.source_url,
                    headline=financial_result_headline(item.record),
                    published_at=item.published_at,
                    title=(
                        f"{legal_name} financial results XBRL - "
                        f"{item.record.period_end.isoformat() if item.record.period_end else 'unknown'}"
                    ),
                    excerpt=(
                        f"Official NSE {item.record.period} XBRL financial results for {symbol}."
                    ),
                    metadata=metadata,
                )
            )
            if disclosure.event_type != "financial_results":
                raise RuntimeError(
                    f"financial-result disclosure classified unexpectedly as "
                    f"{disclosure.event_type}"
                )

            financial_ingestion = await fact_ingestor.ingest_batch(
                security_id=security_id,
                source_id=disclosure.source_id,
                facts=facts,
            )
            evidence_ingestion = await evidence_ingestor.ingest(
                source_id=disclosure.source_id,
                event_id=disclosure.event_id,
                facts=facts,
                document_checksum=fetched.sha256,
                media_type=fetched.media_type,
            )
            documents.append(
                {
                    "period_end": item.record.period_end.isoformat()
                    if item.record.period_end
                    else None,
                    "source_id": str(disclosure.source_id),
                    "event_id": str(disclosure.event_id),
                    "xbrl_url": fetched.source_url,
                    "document_sha256": fetched.sha256,
                    "raw_fact_count": len(facts),
                    "financial_ingestion": financial_ingestion,
                    "evidence_ingestion": evidence_ingestion,
                }
            )
            if position + 1 < len(selected) and document_delay_seconds:
                await asyncio.sleep(document_delay_seconds)

    return {
        "status": "fetched",
        "symbol": symbol,
        "required_periods": required_selected,
        "listing_age_policy_periods": policy_required,
        "selected_period_count": len(selected),
        "documents": documents,
    }
