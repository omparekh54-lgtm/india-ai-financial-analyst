from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor

MATURE_LISTING_AGE_DAYS = 365
MATURE_REQUIRED_DAILY_BARS = 200
MIN_TECHNICAL_AGENT_BARS = 30
RECENT_LISTING_DENSITY = 0.55
RECENT_LISTING_START_GRACE_DAYS = 10
MARKET_FRESHNESS_DAYS = 7


@dataclass(frozen=True)
class MarketHistoryPolicyResult:
    listing_date: date | None
    as_of: date
    required_bars: int
    bar_count: int
    first_bar_date: date | None
    last_bar_date: date | None
    complete: bool
    technical_agent_computable: bool
    recent_listing: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def required_daily_bars(
    listing_date: date | None,
    *,
    as_of: date,
) -> int:
    """Return the minimum sourced daily-bar count justified by listing age.

    Mature or unknown-age securities retain the institutional 200-bar depth contract.
    Recent listings use a conservative 55% calendar-day density floor, capped at 200,
    so production never requires market history that did not exist before the listing.
    """
    if listing_date is None:
        return MATURE_REQUIRED_DAILY_BARS
    age_days = max((as_of - listing_date).days, 0)
    if age_days >= MATURE_LISTING_AGE_DAYS:
        return MATURE_REQUIRED_DAILY_BARS
    return max(1, min(MATURE_REQUIRED_DAILY_BARS, floor(age_days * RECENT_LISTING_DENSITY) + 1))


def evaluate_market_history(
    *,
    listing_date: date | None,
    as_of: date,
    bar_count: int,
    first_bar_date: date | None,
    last_bar_date: date | None,
) -> MarketHistoryPolicyResult:
    if bar_count < 0:
        raise ValueError("bar_count cannot be negative")
    if first_bar_date and last_bar_date and first_bar_date > last_bar_date:
        raise ValueError("first_bar_date cannot be after last_bar_date")

    required = required_daily_bars(listing_date, as_of=as_of)
    recent = bool(
        listing_date is not None
        and max((as_of - listing_date).days, 0) < MATURE_LISTING_AGE_DAYS
    )
    errors: list[str] = []
    warnings: list[str] = []

    if bar_count < required:
        errors.append(f"Sourced daily-bar coverage is incomplete: {bar_count} < {required} required.")
    if last_bar_date is None:
        errors.append("No sourced daily market-bar timestamp is available.")
    elif (as_of - last_bar_date).days > MARKET_FRESHNESS_DAYS:
        errors.append(
            "Sourced daily market history is stale: "
            f"latest bar is {last_bar_date.isoformat()}."
        )

    if recent and listing_date is not None:
        if first_bar_date is None:
            errors.append("Recent listing has no sourced first market bar.")
        elif (first_bar_date - listing_date).days > RECENT_LISTING_START_GRACE_DAYS:
            errors.append(
                "Recent-listing history does not begin close enough to the official listing date."
            )

    computable = bar_count >= MIN_TECHNICAL_AGENT_BARS
    if recent and not computable and not errors:
        warnings.append(
            "Market history is complete for this recent listing, but fewer than 30 sessions exist; "
            "the Technical Agent must return a listing-age limitation instead of fabricating indicators."
        )

    return MarketHistoryPolicyResult(
        listing_date=listing_date,
        as_of=as_of,
        required_bars=required,
        bar_count=bar_count,
        first_bar_date=first_bar_date,
        last_bar_date=last_bar_date,
        complete=not errors,
        technical_agent_computable=computable,
        recent_listing=recent,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
