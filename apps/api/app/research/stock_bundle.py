"""Prepare one security's research data on demand, just before its research job runs.

The research product fetches data when a visitor asks for a stock instead of bulk-ingesting
the whole NSE universe ahead of time. This module is the single place that decides, per
dataset, whether stored data is still good enough or must be fetched now.

Rules this module keeps:

* Real data only. Every write goes through the project's existing official importers, so
  source rows, checksums and provenance classes are exactly what readiness already checks.
  Nothing here creates placeholder rows to make a gate pass.
* Never raises. A failed dataset is recorded with its reason; the worker then re-runs the
  unchanged per-security readiness contract, which decides whether the agents may run.
* Market-wide context (NIFTY 50, India VIX, macro, flows, daily bhavcopy prices) is shared by
  every stock and is refreshed by the scheduled shared-data job, not per request. This module
  only reports whether this security's own price history is fresh.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.connectors.http_fetcher import SourceFetchError
from app.connectors.nse_classification import (
    NseIndustryClassification,
    NseIndustryClassificationFetcher,
)
from app.core.config import Settings
from app.core.financial_history_coverage import load_financial_history_coverage
from app.core.market_history_coverage import load_market_history_coverage
from app.core.peer_metric_coverage import load_peer_metric_coverage
from app.core.security_readiness import accepted_classification_taxonomies
from app.ingestion.classification import persist_nse_classifications
from app.ingestion.derived_metric_ingestion import DerivedSecurityMetricIngestor
from app.ingestion.derived_metrics import derive_peer_metrics, partition_metric_bundle
from app.ingestion.nse_financial_on_demand import ensure_financial_history
from app.ingestion.peer_metric_inputs import load_metric_facts, load_metric_market_close

DatasetStatus = Literal["cached", "fetched", "failed", "stale", "skipped"]

CLASSIFICATION_MAX_AGE_DAYS = 90
MARKET_MAX_AGE_DAYS = 7
CLASSIFICATION_TIMEOUT_SECONDS = 45.0
PEER_METRIC_TIMEOUT_SECONDS = 60.0

ClassificationFetch = Callable[[str, str | None], Awaitable[NseIndustryClassification]]
FinancialFetch = Callable[[AsyncEngine, UUID], Awaitable[dict[str, object]]]


@dataclass(frozen=True)
class DatasetResult:
    name: str
    status: DatasetStatus
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status in {"cached", "fetched"}

    def as_dict(self) -> dict[str, str]:
        return {"dataset": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class StockBundleResult:
    security_id: UUID
    symbol: str
    started_at: datetime
    finished_at: datetime
    datasets: tuple[DatasetResult, ...] = field(default_factory=tuple)

    @property
    def problems(self) -> tuple[DatasetResult, ...]:
        return tuple(item for item in self.datasets if not item.usable)

    def blocker_details(self) -> tuple[str, ...]:
        """Plain-language reasons for every dataset that is not usable, for job failures."""
        return tuple(
            f"data:{item.name}: {item.status}{f' ({item.detail})' if item.detail else ''}"
            for item in self.problems
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "security_id": str(self.security_id),
            "symbol": self.symbol,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_ms": round((self.finished_at - self.started_at).total_seconds() * 1000),
            "datasets": [item.as_dict() for item in self.datasets],
        }


@dataclass(frozen=True)
class _SecurityRow:
    symbol: str
    isin: str | None
    taxonomy: str | None
    classification_retrieved_at: datetime | None


_locks: dict[UUID, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _lock_for(security_id: UUID) -> asyncio.Lock:
    async with _locks_guard:
        lock = _locks.get(security_id)
        if lock is None:
            lock = asyncio.Lock()
            _locks[security_id] = lock
        return lock


async def prepare_security_bundle(
    engine: AsyncEngine,
    security_id: UUID,
    settings: Settings,
    *,
    classification_fetch: ClassificationFetch | None = None,
    financial_fetch: FinancialFetch | None = None,
) -> StockBundleResult:
    """Make this security's stock-specific data current, fetching only what is missing.

    Two concurrent jobs for the same security in one worker process share a single fetch.
    With external data calls disabled, it only reports what is already stored.
    """
    started = datetime.now(UTC)
    lock = await _lock_for(security_id)
    async with lock:
        # Read stored state only after taking the lock, so a job that waited behind another
        # job for the same stock sees what that job just fetched and does not fetch it again.
        security = await _load_security(engine, security_id)
        if security is None:
            return StockBundleResult(
                security_id=security_id,
                symbol=str(security_id),
                started_at=started,
                finished_at=datetime.now(UTC),
                datasets=(
                    DatasetResult("security", "failed", "not a supported NSE EQ security"),
                ),
            )
        fetch_allowed = settings.enable_external_data_calls
        classification, financials = await asyncio.gather(
            _classification_step(
                engine,
                security_id,
                security,
                settings,
                fetch_allowed=fetch_allowed,
                fetch=classification_fetch or _fetch_classification,
            ),
            _financial_step(
                engine,
                security_id,
                fetch_allowed=fetch_allowed,
                fetch=financial_fetch or ensure_financial_history,
            ),
        )
        # Peer metrics are derived from the financial facts and closes just made current.
        peers = await _peer_metric_step(engine, security_id, security.symbol)
        market = await _market_step(engine, security_id)

    return StockBundleResult(
        security_id=security_id,
        symbol=security.symbol,
        started_at=started,
        finished_at=datetime.now(UTC),
        datasets=(classification, financials, peers, market),
    )


async def _load_security(engine: AsyncEngine, security_id: UUID) -> _SecurityRow | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    """
                    select nse_symbol, isin,
                           metadata->>'classification_taxonomy' as taxonomy,
                           metadata->>'classification_retrieved_at' as classification_retrieved_at
                    from securities
                    where id = :security_id
                      and primary_exchange = 'NSE'
                      and coalesce(metadata->>'nse_series', 'EQ') = 'EQ'
                      and nse_symbol is not null
                    """
                ),
                {"security_id": security_id},
            )
        ).mappings().one_or_none()
    if row is None:
        return None
    return _SecurityRow(
        symbol=str(row["nse_symbol"]).strip().upper(),
        isin=str(row["isin"]) if row["isin"] else None,
        taxonomy=str(row["taxonomy"]) if row["taxonomy"] else None,
        classification_retrieved_at=_parse_timestamp(row["classification_retrieved_at"]),
    )


async def _classification_step(
    engine: AsyncEngine,
    security_id: UUID,
    security: _SecurityRow,
    settings: Settings,
    *,
    fetch_allowed: bool,
    fetch: ClassificationFetch,
) -> DatasetResult:
    name = "classification"
    accepted = accepted_classification_taxonomies(settings.classification_policy)
    if security.taxonomy in accepted and _age_days(security.classification_retrieved_at) <= (
        CLASSIFICATION_MAX_AGE_DAYS
    ):
        return DatasetResult(name, "cached", security.taxonomy or "")
    if not fetch_allowed:
        return DatasetResult(name, "skipped", "external data calls are disabled")
    try:
        classification = await asyncio.wait_for(
            fetch(security.symbol, security.isin),
            timeout=CLASSIFICATION_TIMEOUT_SECONDS,
        )
        await persist_nse_classifications(engine, {security_id: classification})
    except TimeoutError:
        return DatasetResult(
            name,
            "failed",
            "NSE quote API timed out; it is often unreachable from non-Indian hosts",
        )
    except (SourceFetchError, ValueError, TypeError) as exc:
        return DatasetResult(name, "failed", _short(exc))
    return DatasetResult(name, "fetched", "NSE four-tier classification")


async def _fetch_classification(symbol: str, isin: str | None) -> NseIndustryClassification:
    async with NseIndustryClassificationFetcher() as fetcher:
        return await fetcher.fetch(symbol, expected_isin=isin)


async def _financial_step(
    engine: AsyncEngine,
    security_id: UUID,
    *,
    fetch_allowed: bool,
    fetch: FinancialFetch,
) -> DatasetResult:
    name = "financials_and_results_filings"
    coverage = await load_financial_history_coverage(engine, security_id=security_id)
    if coverage.complete_securities >= 1:
        return DatasetResult(name, "cached")
    if not fetch_allowed:
        return DatasetResult(name, "skipped", "external data calls are disabled")
    # ensure_financial_history never raises and bounds its own runtime.
    outcome = await fetch(engine, security_id)
    status = str(outcome.get("status") or "error")
    if status in {"fetched", "cache_hit"}:
        refreshed = await load_financial_history_coverage(engine, security_id=security_id)
        if refreshed.complete_securities >= 1:
            return DatasetResult(name, "fetched" if status == "fetched" else "cached")
        return DatasetResult(
            name,
            "stale",
            "stored results do not yet meet the period or freshness rule",
        )
    return DatasetResult(name, "failed", _short(outcome.get("error") or status))


async def _peer_metric_step(engine: AsyncEngine, security_id: UUID, symbol: str) -> DatasetResult:
    name = "peer_metrics"
    coverage = await load_peer_metric_coverage(engine, security_id=security_id)
    if coverage.complete_securities >= 1:
        return DatasetResult(name, "cached")
    try:
        return await asyncio.wait_for(
            _derive_peer_metrics(engine, security_id, symbol),
            timeout=PEER_METRIC_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        return DatasetResult(name, "failed", "peer metric derivation timed out")
    except (ValueError, TypeError, ArithmeticError) as exc:
        return DatasetResult(name, "failed", _short(exc))


async def _derive_peer_metrics(
    engine: AsyncEngine,
    security_id: UUID,
    symbol: str,
) -> DatasetResult:
    name = "peer_metrics"
    facts = await load_metric_facts(engine, security_id)
    if not facts:
        return DatasetResult(name, "failed", "no source-linked financial facts to derive from")
    market = await load_metric_market_close(engine, security_id)
    bundle = derive_peer_metrics(facts, market=market)
    if not bundle.metrics:
        return DatasetResult(name, "failed", "stored facts do not support any comparable metric")
    ingestor = DerivedSecurityMetricIngestor(engine)
    for partition in partition_metric_bundle(bundle):
        await ingestor.ingest(security_id=security_id, symbol=symbol, bundle=partition)
    refreshed = await load_peer_metric_coverage(engine, security_id=security_id)
    if refreshed.complete_securities >= 1:
        return DatasetResult(name, "fetched", f"{len(bundle.metrics)} derived metrics")
    return DatasetResult(
        name,
        "stale",
        "derived metrics stored, but fewer than the required comparable set or not approved",
    )


async def _market_step(engine: AsyncEngine, security_id: UUID) -> DatasetResult:
    name = "price_history"
    coverage = await load_market_history_coverage(engine, security_id=security_id)
    async with engine.connect() as connection:
        latest = await connection.scalar(
            text(
                """
                select max(ts) from market_bars
                where security_id = :security_id
                  and source_id is not null
                  and interval in ('1d', 'day', 'daily')
                """
            ),
            {"security_id": security_id},
        )
    if latest is None:
        return DatasetResult(name, "failed", "no sourced daily price history stored")
    age = _age_days(latest)
    if coverage.complete_securities < 1:
        return DatasetResult(name, "stale", "daily history is shorter than the required window")
    if age > MARKET_MAX_AGE_DAYS:
        return DatasetResult(
            name,
            "stale",
            f"latest bar is {age} days old; refreshed by the daily shared-data job",
        )
    return DatasetResult(name, "cached", f"latest bar {latest.date().isoformat()}")


def _age_days(value: datetime | None) -> int:
    if value is None:
        return 1_000_000
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max((datetime.now(UTC) - value).days, 0)


def _parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _short(value: object, limit: int = 160) -> str:
    message = str(value).strip() or type(value).__name__
    return message if len(message) <= limit else message[: limit - 1] + "…"
