from app.workers.official_feeds import production_feed_block_reason


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


def test_production_accepts_only_explicitly_cleared_review_flag() -> None:
    assert (
        production_feed_block_reason(
            {"production_requires_licensing_review": False},
            app_env="production",
        )
        is None
    )
    assert (
        production_feed_block_reason(
            {"production_requires_licensing_review": "false"},
            app_env="production",
        )
        is None
    )


def test_truthy_string_review_flag_is_blocked_in_production() -> None:
    for value in ("true", "1", "yes", "on"):
        assert (
            production_feed_block_reason(
                {"production_requires_licensing_review": value},
                app_env="production",
            )
            is not None
        )
