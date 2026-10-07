"""On-demand research data: fetch for the requested stock, then apply the unchanged gate."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.research_gate import SecurityNotReadyError
from app.core.security_readiness import (
    accepted_classification_taxonomies,
    evaluate_security_readiness,
)
from app.research import stock_bundle
from app.research.stock_bundle import DatasetResult, StockBundleResult, prepare_security_bundle
from app.workers.research_jobs import ResearchJobWorker
from tests.test_security_readiness import NOW, _complete_coverage, _corpus, _settings


def _security(**overrides: object) -> stock_bundle._SecurityRow:
    base: dict[str, object] = {
        "symbol": "TCS",
        "isin": "INE467B01029",
        "taxonomy": None,
        "classification_retrieved_at": None,
    }
    base.update(overrides)
    return stock_bundle._SecurityRow(**base)  # type: ignore[arg-type]


def _coverage(complete: int) -> SimpleNamespace:
    return SimpleNamespace(complete_securities=complete)


@pytest.fixture
def stored(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Stub every database read the bundle makes; tests flip individual datasets."""
    state: dict[str, object] = {
        "security": _security(),
        "financial": 1,
        "peer": 1,
        "market_steps": DatasetResult("price_history", "cached"),
    }

    async def load_security(engine: object, security_id: object) -> object:
        return state["security"]

    async def financial(engine: object, *, security_id: object) -> SimpleNamespace:
        return _coverage(int(state["financial"]))  # type: ignore[call-overload]

    async def peers(engine: object, *, security_id: object) -> SimpleNamespace:
        return _coverage(int(state["peer"]))  # type: ignore[call-overload]

    async def market(engine: object, security_id: object) -> DatasetResult:
        return state["market_steps"]  # type: ignore[return-value]

    monkeypatch.setattr(stock_bundle, "_load_security", load_security)
    monkeypatch.setattr(stock_bundle, "load_financial_history_coverage", financial)
    monkeypatch.setattr(stock_bundle, "load_peer_metric_coverage", peers)
    monkeypatch.setattr(stock_bundle, "_market_step", market)
    monkeypatch.setattr(stock_bundle, "persist_nse_classifications", AsyncMock(return_value=1))
    return state


def _statuses(result: StockBundleResult) -> dict[str, str]:
    return {item.name: item.status for item in result.datasets}


@pytest.mark.asyncio
async def test_fresh_stored_data_is_reused_without_any_fetch(stored: dict[str, object]) -> None:
    stored["security"] = _security(
        taxonomy="NSE_INDICES_4_TIER",
        classification_retrieved_at=datetime.now(UTC) - timedelta(days=3),
    )
    classification_fetch = AsyncMock()
    financial_fetch = AsyncMock()

    result = await prepare_security_bundle(
        object(),  # type: ignore[arg-type]
        uuid4(),
        _settings(),
        classification_fetch=classification_fetch,
        financial_fetch=financial_fetch,
    )

    assert set(_statuses(result).values()) == {"cached"}
    classification_fetch.assert_not_awaited()
    financial_fetch.assert_not_awaited()
    assert result.blocker_details() == ()


@pytest.mark.asyncio
async def test_missing_classification_is_fetched_and_persisted(stored: dict[str, object]) -> None:
    classification = SimpleNamespace(symbol="TCS")
    classification_fetch = AsyncMock(return_value=classification)

    result = await prepare_security_bundle(
        object(),  # type: ignore[arg-type]
        uuid4(),
        _settings(),
        classification_fetch=classification_fetch,
        financial_fetch=AsyncMock(),
    )

    classification_fetch.assert_awaited_once_with("TCS", "INE467B01029")
    stock_bundle.persist_nse_classifications.assert_awaited_once()  # type: ignore[attr-defined]
    assert _statuses(result)["classification"] == "fetched"


@pytest.mark.asyncio
async def test_unreachable_nse_is_reported_not_raised(
    stored: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stock_bundle, "CLASSIFICATION_TIMEOUT_SECONDS", 0.01)

    async def hang(symbol: str, isin: str | None) -> object:
        await asyncio.sleep(1)
        raise AssertionError("unreachable")

    result = await prepare_security_bundle(
        object(),  # type: ignore[arg-type]
        uuid4(),
        _settings(),
        classification_fetch=hang,
        financial_fetch=AsyncMock(),
    )

    statuses = _statuses(result)
    assert statuses["classification"] == "failed"
    # One failed dataset does not stop the others from being evaluated.
    assert statuses["financials_and_results_filings"] == "cached"
    assert any("classification" in line for line in result.blocker_details())
    stock_bundle.persist_nse_classifications.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_external_calls_disabled_only_reports_stored_state(
    stored: dict[str, object],
) -> None:
    stored["financial"] = 0
    classification_fetch = AsyncMock()
    financial_fetch = AsyncMock()

    result = await prepare_security_bundle(
        object(),  # type: ignore[arg-type]
        uuid4(),
        _settings(enable_external_data_calls=False),
        classification_fetch=classification_fetch,
        financial_fetch=financial_fetch,
    )

    statuses = _statuses(result)
    assert statuses["classification"] == "skipped"
    assert statuses["financials_and_results_filings"] == "skipped"
    classification_fetch.assert_not_awaited()
    financial_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_financial_fetch_that_leaves_history_incomplete_is_stale(
    stored: dict[str, object],
) -> None:
    stored["financial"] = 0
    financial_fetch = AsyncMock(return_value={"status": "cache_hit"})

    result = await prepare_security_bundle(
        object(),  # type: ignore[arg-type]
        uuid4(),
        _settings(),
        classification_fetch=AsyncMock(return_value=SimpleNamespace(symbol="TCS")),
        financial_fetch=financial_fetch,
    )

    assert _statuses(result)["financials_and_results_filings"] == "stale"


@pytest.mark.asyncio
async def test_unsupported_security_fails_without_fetching(stored: dict[str, object]) -> None:
    stored["security"] = None
    classification_fetch = AsyncMock()

    result = await prepare_security_bundle(
        object(),  # type: ignore[arg-type]
        uuid4(),
        _settings(),
        classification_fetch=classification_fetch,
    )

    assert _statuses(result) == {"security": "failed"}
    classification_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_requests_for_one_stock_share_one_fetch(
    stored: dict[str, object],
) -> None:
    security_id = uuid4()
    calls = 0

    async def fetch(symbol: str, isin: str | None) -> object:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        stored["security"] = _security(
            taxonomy="NSE_INDICES_4_TIER",
            classification_retrieved_at=datetime.now(UTC),
        )
        return SimpleNamespace(symbol=symbol)

    await asyncio.gather(
        *(
            prepare_security_bundle(
                object(),  # type: ignore[arg-type]
                security_id,
                _settings(),
                classification_fetch=fetch,
                financial_fetch=AsyncMock(),
            )
            for _ in range(2)
        )
    )

    assert calls == 1


def test_security_freshness_uses_its_own_latest_bar_not_the_corpus() -> None:
    """A fresh corpus must not hide that this one security's prices are stale."""
    readiness = evaluate_security_readiness(
        uuid4(),
        "STALECO",
        _complete_coverage(latest_security_market_bar=NOW - timedelta(days=30)),
        _corpus(latest_market_bar=NOW - timedelta(days=1)),
        _settings(),
        as_of=NOW,
    )
    assert not readiness.ready
    assert "live_market_microstructure" in readiness.blocking_agents
    assert any("stale beyond 7 days" in line for line in readiness.blockers())


def test_security_with_fresh_own_bars_passes_market_freshness() -> None:
    readiness = evaluate_security_readiness(
        uuid4(),
        "FRESHCO",
        _complete_coverage(latest_security_market_bar=NOW - timedelta(days=1)),
        _corpus(latest_market_bar=NOW - timedelta(days=1)),
        _settings(),
        as_of=NOW,
    )
    assert readiness.ready, readiness.blockers()


def test_classification_policy_defaults_to_strict_four_tier() -> None:
    assert Settings().classification_policy == "nse_four_tier"
    assert accepted_classification_taxonomies("nse_four_tier") == ("NSE_INDICES_4_TIER",)
    assert accepted_classification_taxonomies("nse_sector_or_better") == (
        "NSE_INDICES_4_TIER",
        "NSE_TOTAL_MARKET_SECTOR_ONLY",
    )


def _worker_with_job(security_id: object) -> tuple[ResearchJobWorker, object]:
    job_id = uuid4()
    worker = object.__new__(ResearchJobWorker)
    worker.engine = object()  # type: ignore[assignment]
    worker.queue = AsyncMock()
    worker.queue.claim_next.return_value = {
        "id": job_id,
        "query": "TCS",
        "mode": "full_analysis",
        "requested_by": uuid4(),
        "security_id": security_id,
        "metadata": {"analysis_depth": "standard", "data_preparation": "on_demand"},
    }
    worker.settings = _settings(app_env="production")
    worker.service = AsyncMock()
    return worker, job_id


def _bundle(security_id: object, *datasets: DatasetResult) -> StockBundleResult:
    now = datetime.now(UTC)
    return StockBundleResult(
        security_id=security_id,  # type: ignore[arg-type]
        symbol="TCS",
        started_at=now,
        finished_at=now,
        datasets=datasets,
    )


@pytest.mark.asyncio
async def test_worker_fetches_then_gates_then_runs_agents() -> None:
    events: list[str] = []
    security_id = uuid4()
    worker, _ = _worker_with_job(security_id)
    worker.service.progress.set_stage.side_effect = lambda *args: events.append(args[1])
    worker.service.execute_existing.side_effect = lambda **kwargs: events.append(
        f"execute:{kwargs['context']['data_bundle']['symbol']}"
    )

    async def prepare(*args: object, **kwargs: object) -> StockBundleResult:
        events.append("prepare")
        return _bundle(security_id, DatasetResult("classification", "fetched"))

    async def gate(*args: object, **kwargs: object) -> None:
        events.append("gate")

    with (
        patch("app.workers.research_jobs.prepare_security_bundle", side_effect=prepare),
        patch("app.workers.research_jobs.enforce_security_research_ready", side_effect=gate),
    ):
        assert await worker.poll_once() is True

    assert events == ["fetching_data", "prepare", "gate", "execute:TCS"]
    worker.queue.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_fails_job_with_missing_data_listed_first() -> None:
    security_id = uuid4()
    worker, job_id = _worker_with_job(security_id)
    readiness = evaluate_security_readiness(
        security_id,  # type: ignore[arg-type]
        "TCS",
        _complete_coverage(classified_securities=0),
        _corpus(),
        _settings(),
        as_of=NOW,
    )

    async def prepare(*args: object, **kwargs: object) -> StockBundleResult:
        return _bundle(
            security_id,
            DatasetResult("classification", "failed", "NSE quote API timed out"),
            DatasetResult("price_history", "cached"),
        )

    async def gate(*args: object, **kwargs: object) -> None:
        raise SecurityNotReadyError(readiness)

    with (
        patch("app.workers.research_jobs.prepare_security_bundle", side_effect=prepare),
        patch("app.workers.research_jobs.enforce_security_research_ready", side_effect=gate),
    ):
        assert await worker.poll_once() is True

    worker.service.execute_existing.assert_not_awaited()
    kwargs = worker.queue.mark_failed.await_args.kwargs
    assert kwargs["failure_code"] == "security_not_ready"
    assert "industry_peer_intelligence" in kwargs["blocking_agents"]
    details = kwargs["blocker_details"]
    assert details[0] == "data:classification: failed (NSE quote API timed out)"
    assert any(line.startswith("industry_peer_intelligence:") for line in details[1:])
    worker.queue.mark_failed.assert_awaited_once()
    assert worker.queue.mark_failed.await_args.args == (job_id,)
