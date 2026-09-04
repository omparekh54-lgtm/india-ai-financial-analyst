from app.core.operations_health import evaluate_live_market_operations


def test_disabled_live_market_does_not_create_false_alerts() -> None:
    assert evaluate_live_market_operations(
        enabled=False,
        active_subscriptions=2,
        active_leases=0,
        fresh_quotes=0,
        stale_subscriptions=2,
    ) == ((), ())


def test_enabled_live_market_without_subscriptions_is_visible_but_not_blocking() -> None:
    errors, warnings = evaluate_live_market_operations(
        enabled=True,
        active_subscriptions=0,
        active_leases=0,
        fresh_quotes=0,
        stale_subscriptions=0,
    )
    assert errors == ()
    assert warnings == ("Live market is enabled but has no active user subscriptions.",)


def test_active_live_market_requires_lease_and_fresh_quotes() -> None:
    errors, warnings = evaluate_live_market_operations(
        enabled=True,
        active_subscriptions=3,
        active_leases=0,
        fresh_quotes=0,
        stale_subscriptions=3,
    )
    assert warnings == ()
    assert errors == (
        "Live market has active subscriptions but no active stream lease.",
        "Live market has active subscriptions but no fresh quotes.",
        "Active live-market subscriptions without fresh quotes: 3",
    )


def test_healthy_live_market_has_no_alerts() -> None:
    assert evaluate_live_market_operations(
        enabled=True,
        active_subscriptions=3,
        active_leases=1,
        fresh_quotes=3,
        stale_subscriptions=0,
    ) == ((), ())
