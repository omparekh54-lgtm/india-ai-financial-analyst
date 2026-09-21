from datetime import date
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.workers.research_jobs import ResearchJobWorker
from scripts.backfill_yfinance_market_history import HistoryTarget, refresh_start_day


def test_recent_refresh_does_not_request_listing_history():
    target = HistoryTarget(uuid4(), "TEST", "Test", "NSE", date(2000, 1, 1))
    assert refresh_start_day(target, end_day=date(2026, 9, 7), recent_days=365) == date(2025, 9, 7)
    assert target.checkpoint is None


def test_recent_refresh_resumes_with_overlap():
    target = HistoryTarget(
        uuid4(), "TEST", "Test", "NSE", date(2000, 1, 1), {"recent_last_bar_date": "2026-09-04"}
    )
    assert refresh_start_day(target, end_day=date(2026, 9, 7), recent_days=365) == date(2026, 8, 28)


def test_new_listing_never_requests_prelisting_dates():
    target = HistoryTarget(uuid4(), "TEST", "Test", "NSE", date(2026, 9, 1))
    assert refresh_start_day(target, end_day=date(2026, 9, 7), recent_days=365) == date(2026, 9, 1)


@pytest.mark.asyncio
async def test_worker_database_retries_are_bounded_and_sanitized():
    worker = object.__new__(ResearchJobWorker)
    worker.queue = AsyncMock()
    worker.settings = type("Settings", (), {"max_research_job_seconds": 240})()
    worker.queue.requeue_stale_running_jobs.side_effect = OperationalError(
        "secret SQL", {"private": "value"}, Exception("private connection")
    )
    with (
        patch("app.workers.research_jobs.asyncio.sleep", new_callable=AsyncMock) as sleep,
        patch("app.workers.research_jobs.sentry_sdk.capture_message") as capture,
        patch("app.workers.research_jobs.sentry_sdk.flush"),
    ):
        with pytest.raises(RuntimeError, match="five database failures") as error:
            await worker.run_forever()
        assert "secret" not in str(error.value)
        assert worker.queue.requeue_stale_running_jobs.await_count == 5
        assert [call.args[0] for call in sleep.await_args_list] == [5, 10, 20, 40]
        capture.assert_called_once()


@pytest.mark.asyncio
async def test_worker_prepares_financials_before_rechecking_readiness():
    events: list[str] = []
    job_id = uuid4()
    security_id = uuid4()
    worker = object.__new__(ResearchJobWorker)
    worker.engine = object()
    worker.queue = AsyncMock()
    worker.queue.claim_next.return_value = {
        "id": job_id,
        "query": "HDFCBANK",
        "mode": "full_analysis",
        "requested_by": uuid4(),
        "security_id": security_id,
        "metadata": {
            "analysis_depth": "standard",
            "preparation_required": ["financial_history"],
        },
    }
    worker.settings = type(
        "Settings",
        (),
        {"app_env": "production", "enable_external_data_calls": True},
    )()
    worker.service = AsyncMock()
    worker.service.progress.set_stage.side_effect = lambda *args: events.append("progress")
    worker.service.execute_existing.side_effect = lambda **kwargs: events.append("execute")

    async def prepare(*args, **kwargs):
        events.append("prepare")
        return {"status": "fetched"}

    async def gate(*args, **kwargs):
        events.append("gate")

    with (
        patch("app.workers.research_jobs.ensure_financial_history", side_effect=prepare),
        patch("app.workers.research_jobs.enforce_security_research_ready", side_effect=gate),
    ):
        assert await worker.poll_once() is True

    assert events == ["progress", "prepare", "gate", "execute"]
    worker.queue.mark_failed.assert_not_awaited()
