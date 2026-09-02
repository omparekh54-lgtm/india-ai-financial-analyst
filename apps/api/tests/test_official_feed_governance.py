from __future__ import annotations

from uuid import uuid4

from app.repositories.official_feeds import ClaimedFeed, OfficialFeed
from app.workers.official_feeds import OfficialFeedWorker, production_feed_block_reason


def test_development_allows_feed_pending_licensing_review() -> None:
    reason = production_feed_block_reason(
        {"production_requires_licensing_review": True},
        app_env="development",
    )
    assert reason is None


def test_production_blocks_feed_pending_licensing_review() -> None:
    reason = production_feed_block_reason(
        {"production_requires_licensing_review": True},
        app_env="production",
    )
    assert reason is not None
    assert "licensing" in reason.lower()


def test_production_requires_approval_reference_after_review_flag_is_cleared() -> None:
    reason = production_feed_block_reason(
        {"production_requires_licensing_review": False},
        app_env="production",
    )
    assert reason is not None
    assert "approval reference" in reason.lower()

    assert (
        production_feed_block_reason(
            {
                "production_requires_licensing_review": False,
                "production_approval_reference": "approval-record-2026-08-31",
            },
            app_env="production",
        )
        is None
    )


def test_feed_without_review_gate_is_not_artificially_blocked() -> None:
    assert production_feed_block_reason({}, app_env="production") is None


def test_truthy_string_review_flag_is_blocked_in_production() -> None:
    for value in ("true", "1", "yes", "on"):
        assert (
            production_feed_block_reason(
                {"production_requires_licensing_review": value},
                app_env="production",
            )
            is not None
        )


async def test_blocked_production_claim_never_reaches_fetch_dispatch_path() -> None:
    claim = ClaimedFeed(
        feed=OfficialFeed(
            id=uuid4(),
            name="NSE public corporate announcements development template",
            provider="NSE",
            feed_type="exchange_disclosures",
            source_url="https://www.nseindia.com/api/corporate-announcements?index=equities",
            exchange="NSE",
            identifier=None,
            title="NSE corporate announcements",
            parser_config={"production_requires_licensing_review": True},
            poll_interval_seconds=900,
            etag=None,
            last_modified=None,
        ),
        run_id=uuid4(),
    )

    class RepositoryStub:
        def __init__(self) -> None:
            self.blocked_reason: str | None = None

        async def claim_due(self, *, limit: int = 4) -> list[ClaimedFeed]:
            assert limit == 4
            return [claim]

        async def block(self, claimed: ClaimedFeed, *, reason: str) -> None:
            assert claimed == claim
            self.blocked_reason = reason

    repository = RepositoryStub()
    worker = object.__new__(OfficialFeedWorker)
    worker.external_data_enabled = True
    worker.app_env = "production"
    worker.repository = repository  # type: ignore[assignment]

    async def forbidden_run_claim(_: ClaimedFeed) -> dict[str, object]:
        raise AssertionError("blocked feed reached fetch/dispatch path")

    worker._run_claim = forbidden_run_claim  # type: ignore[method-assign]
    result = await worker.run_once(limit=4)

    assert repository.blocked_reason is not None
    assert result["blocked_count"] == 1
    assert result["success_count"] == 0
    assert result["failed_count"] == 0
    assert result["results"] == [
        {
            "feed_id": str(claim.feed.id),
            "name": claim.feed.name,
            "status": "blocked",
            "reason": repository.blocked_reason,
            "network_request_performed": False,
        }
    ]
