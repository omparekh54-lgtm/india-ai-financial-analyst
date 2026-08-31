from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor

MATURE_REQUIRED_PERIODS = 8
MIN_CANONICAL_FACT_TYPES = 6
REPORTING_GRACE_DAYS = 60
APPROX_QUARTER_DAYS = 91
FINANCIAL_FRESHNESS_DAYS = 200


@dataclass(frozen=True)
class FinancialHistoryPolicyResult:
    listing_date: date | None
    as_of: date
    required_periods: int
    period_count: int
    fact_type_count: int
    latest_period_end: date | None
    complete: bool
    history_limited_recent_listing: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def required_financial_periods(
    listing_date: date | None,
    *,
    as_of: date,
) -> int:
    """Return sourced reporting periods reasonably available since NSE listing.

    Unknown-age securities retain the institutional eight-period depth contract. Recent listings
    get a 60-day first-report grace and roughly one additional expected reporting opportunity per
    quarter, capped at eight. At least one source-backed period is always required before the
    Financials Agent can be considered data-ready.
    """
    if listing_date is None:
        return MATURE_REQUIRED_PERIODS
    age_days = max((as_of - listing_date).days, 0)
    elapsed_after_grace = max(age_days - REPORTING_GRACE_DAYS, 0)
    expected = floor(elapsed_after_grace / APPROX_QUARTER_DAYS) + 1
    return max(1, min(MATURE_REQUIRED_PERIODS, expected))


def evaluate_financial_history(
    *,
    listing_date: date | None,
    as_of: date,
    period_count: int,
    fact_type_count: int,
    latest_period_end: date | None,
) -> FinancialHistoryPolicyResult:
    if period_count < 0:
        raise ValueError("period_count cannot be negative")
    if fact_type_count < 0:
        raise ValueError("fact_type_count cannot be negative")
    if latest_period_end is not None and latest_period_end > as_of:
        raise ValueError("latest_period_end cannot be after as_of")

    required = required_financial_periods(listing_date, as_of=as_of)
    history_limited = required < MATURE_REQUIRED_PERIODS
    errors: list[str] = []
    warnings: list[str] = []

    if period_count < required:
        errors.append(
            f"Sourced financial-history coverage is incomplete: {period_count} < {required} "
            "required distinct reporting periods."
        )
    if fact_type_count < MIN_CANONICAL_FACT_TYPES:
        errors.append(
            "Sourced financial history needs at least "
            f"{MIN_CANONICAL_FACT_TYPES} distinct canonical fact types."
        )
    if latest_period_end is None:
        errors.append("No sourced financial reporting period is available.")
    elif (as_of - latest_period_end).days > FINANCIAL_FRESHNESS_DAYS:
        errors.append(
            "Sourced financial history is stale: latest period end is "
            f"{latest_period_end.isoformat()}."
        )

    if history_limited and not errors:
        warnings.append(
            "Financial history is complete for the available post-listing reporting window but "
            f"contains fewer than {MATURE_REQUIRED_PERIODS} periods; long-horizon trend analysis "
            "must disclose the listing-age limitation."
        )

    return FinancialHistoryPolicyResult(
        listing_date=listing_date,
        as_of=as_of,
        required_periods=required,
        period_count=period_count,
        fact_type_count=fact_type_count,
        latest_period_end=latest_period_end,
        complete=not errors,
        history_limited_recent_listing=history_limited and not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
