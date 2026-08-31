from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.ingestion.metrics import SecurityMetricInput

PEER_USABLE_METRICS = frozenset(
    {
        "revenue_growth",
        "ebitda_margin",
        "roce",
        "roe",
        "roa",
        "pe",
        "pb",
        "ev_ebitda",
        "market_cap",
        "gross_npa_pct",
        "net_npa_pct",
        "nim_pct",
        "casa_ratio_pct",
        "capital_adequacy_pct",
        "aum_growth",
        "vnb_margin_pct",
        "solvency_ratio_pct",
        "combined_ratio_pct",
        "constant_currency_growth_pct",
        "attrition_pct",
        "market_share_pct",
        "capacity_utilization_pct",
    }
)

_GROWTH_BASES = ("revenue", "net_interest_income", "gross_written_premium", "aum")
_PASSTHROUGH = {
    "gross_npa_pct": "gross_npa_pct",
    "net_npa_pct": "net_npa_pct",
    "nim_pct": "nim_pct",
    "casa_ratio_pct": "casa_ratio_pct",
    "capital_adequacy_pct": "capital_adequacy_pct",
    "vnb_margin_pct": "vnb_margin_pct",
    "solvency_ratio_pct": "solvency_ratio_pct",
    "combined_ratio_pct": "combined_ratio_pct",
    "constant_currency_growth_pct": "constant_currency_growth_pct",
    "attrition_pct": "attrition_pct",
    "market_share_pct": "market_share_pct",
    "capacity_utilization_pct": "capacity_utilization_pct",
}


@dataclass(frozen=True)
class MetricFinancialFact:
    fact_name: str
    period_end: date
    period_type: str
    value: Decimal
    unit: str | None
    source_id: UUID


@dataclass(frozen=True)
class MetricMarketClose:
    as_of_date: date
    price: Decimal
    source_id: UUID


@dataclass(frozen=True)
class DerivedMetricBundle:
    metrics: tuple[SecurityMetricInput, ...]
    upstream_source_ids: tuple[UUID, ...]
    checksum: str


def derive_peer_metrics(
    facts: list[MetricFinancialFact],
    *,
    market: MetricMarketClose | None = None,
) -> DerivedMetricBundle:
    """Derive comparable metrics only from source-linked normalized facts and market data.

    The function is deterministic and intentionally conservative. It refuses calculations whose
    periods or units do not align rather than estimating missing inputs. Each metric records its
    formula/basis and upstream source IDs so a persisted derived source can remain auditable.
    """
    if any(fact.source_id is None for fact in facts):  # pragma: no cover - UUID type guard
        raise ValueError("all financial facts must be source-linked")
    if market is not None and market.price <= 0:
        raise ValueError("market price must be positive")

    by_name: dict[str, list[MetricFinancialFact]] = {}
    for fact in facts:
        by_name.setdefault(fact.fact_name, []).append(fact)
    for rows in by_name.values():
        rows.sort(key=lambda item: item.period_end, reverse=True)

    derived: dict[str, SecurityMetricInput] = {}
    source_ids: set[UUID] = set()

    growth = _growth_metric(by_name)
    if growth is not None:
        _put(derived, source_ids, growth)

    for metric in (
        _margin_metric(by_name),
        _roce_metric(by_name),
        _roe_metric(by_name),
        _roa_metric(by_name),
        _aum_growth_metric(by_name),
    ):
        if metric is not None:
            _put(derived, source_ids, metric)

    for fact_name, metric_name in _PASSTHROUGH.items():
        latest = _latest(by_name, fact_name)
        if latest is None:
            continue
        value = latest.value
        unit = latest.unit
        _put(
            derived,
            source_ids,
            _metric(
                metric_name,
                latest.period_end,
                value,
                unit,
                "source_fact_passthrough",
                [latest],
                basis_fact=fact_name,
            ),
        )

    if market is not None:
        for metric in (
            _pe_metric(by_name, market),
            _pb_metric(by_name, market),
            _market_cap_metric(by_name, market),
        ):
            if metric is not None:
                _put(derived, source_ids, metric)

    metrics = tuple(sorted(derived.values(), key=lambda item: item.metric_name))
    source_ids_sorted = tuple(sorted(source_ids, key=str))
    payload = {
        "calculation_version": 1,
        "metrics": [
            {
                "metric_name": item.metric_name,
                "as_of_date": item.as_of_date.isoformat(),
                "value": str(item.value),
                "unit": item.unit,
                "metadata": item.metadata,
            }
            for item in metrics
        ],
        "upstream_source_ids": [str(value) for value in source_ids_sorted],
    }
    checksum = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return DerivedMetricBundle(
        metrics=metrics,
        upstream_source_ids=source_ids_sorted,
        checksum=checksum,
    )


def _growth_metric(
    by_name: dict[str, list[MetricFinancialFact]],
) -> SecurityMetricInput | None:
    for basis in _GROWTH_BASES:
        latest = _latest(by_name, basis)
        if latest is None or latest.value == 0:
            continue
        previous = _comparable_previous(by_name.get(basis, []), latest)
        if previous is None or previous.value == 0 or not _units_compatible(latest.unit, previous.unit):
            continue
        value = latest.value / previous.value - Decimal("1")
        return _metric(
            "revenue_growth",
            latest.period_end,
            value,
            "ratio",
            "current_over_comparable_previous_minus_one",
            [latest, previous],
            basis_fact=basis,
            comparison_period_end=previous.period_end.isoformat(),
        )
    return None


def _margin_metric(by_name: dict[str, list[MetricFinancialFact]]) -> SecurityMetricInput | None:
    pair = _same_period_pair(by_name, "ebitda", "revenue")
    if pair is None:
        return None
    ebitda, revenue = pair
    if revenue.value == 0 or not _units_compatible(ebitda.unit, revenue.unit):
        return None
    return _metric(
        "ebitda_margin",
        revenue.period_end,
        ebitda.value / revenue.value,
        "ratio",
        "ebitda_divided_by_revenue",
        [ebitda, revenue],
    )


def _roce_metric(by_name: dict[str, list[MetricFinancialFact]]) -> SecurityMetricInput | None:
    latest_ebit = _latest(by_name, "ebit")
    if latest_ebit is None:
        return None
    assets = _at_or_before(by_name.get("total_assets", []), latest_ebit.period_end)
    liabilities = _at_or_before(by_name.get("current_liabilities", []), latest_ebit.period_end)
    if assets is None or liabilities is None:
        return None
    if not _units_compatible(latest_ebit.unit, assets.unit, liabilities.unit):
        return None
    capital_employed = assets.value - liabilities.value
    if capital_employed <= 0:
        return None
    return _metric(
        "roce",
        latest_ebit.period_end,
        latest_ebit.value / capital_employed,
        "ratio",
        "ebit_divided_by_total_assets_minus_current_liabilities",
        [latest_ebit, assets, liabilities],
    )


def _roe_metric(by_name: dict[str, list[MetricFinancialFact]]) -> SecurityMetricInput | None:
    reported = _latest(by_name, "roe_pct")
    if reported is not None:
        return _metric(
            "roe",
            reported.period_end,
            _percent_to_ratio(reported.value, reported.unit),
            "ratio",
            "reported_roe_normalized_to_ratio",
            [reported],
        )
    return _return_metric(by_name, denominator_name="total_equity", metric_name="roe")


def _roa_metric(by_name: dict[str, list[MetricFinancialFact]]) -> SecurityMetricInput | None:
    reported = _latest(by_name, "roa_pct")
    if reported is not None:
        return _metric(
            "roa",
            reported.period_end,
            _percent_to_ratio(reported.value, reported.unit),
            "ratio",
            "reported_roa_normalized_to_ratio",
            [reported],
        )
    return _return_metric(by_name, denominator_name="total_assets", metric_name="roa")


def _return_metric(
    by_name: dict[str, list[MetricFinancialFact]],
    *,
    denominator_name: str,
    metric_name: str,
) -> SecurityMetricInput | None:
    annual_pat = next(
        (fact for fact in by_name.get("pat", []) if fact.period_type in {"annual", "ttm"}),
        None,
    )
    if annual_pat is None:
        return None
    denominator = _at_or_before(by_name.get(denominator_name, []), annual_pat.period_end)
    if denominator is None or denominator.value <= 0:
        return None
    if not _units_compatible(annual_pat.unit, denominator.unit):
        return None
    return _metric(
        metric_name,
        annual_pat.period_end,
        annual_pat.value / denominator.value,
        "ratio",
        f"pat_divided_by_{denominator_name}",
        [annual_pat, denominator],
    )


def _aum_growth_metric(by_name: dict[str, list[MetricFinancialFact]]) -> SecurityMetricInput | None:
    rows = by_name.get("aum", [])
    latest = rows[0] if rows else None
    if latest is None:
        return None
    previous = _comparable_previous(rows, latest)
    if previous is None or previous.value == 0 or not _units_compatible(latest.unit, previous.unit):
        return None
    return _metric(
        "aum_growth",
        latest.period_end,
        latest.value / previous.value - Decimal("1"),
        "ratio",
        "aum_current_over_comparable_previous_minus_one",
        [latest, previous],
        comparison_period_end=previous.period_end.isoformat(),
    )


def _pe_metric(
    by_name: dict[str, list[MetricFinancialFact]], market: MetricMarketClose
) -> SecurityMetricInput | None:
    eps = next(
        (
            fact
            for name in ("eps_diluted", "eps_basic")
            for fact in by_name.get(name, [])
            if fact.period_type in {"annual", "ttm"} and fact.value > 0
        ),
        None,
    )
    if eps is None:
        return None
    return SecurityMetricInput(
        metric_name="pe",
        as_of_date=market.as_of_date,
        value=market.price / eps.value,
        unit="multiple",
        metadata=_metadata(
            "market_price_divided_by_annual_or_ttm_eps",
            [eps.source_id, market.source_id],
            financial_period_end=eps.period_end.isoformat(),
        ),
    )


def _pb_metric(
    by_name: dict[str, list[MetricFinancialFact]], market: MetricMarketClose
) -> SecurityMetricInput | None:
    bvps = _latest(by_name, "book_value_per_share")
    if bvps is None or bvps.value <= 0:
        return None
    return SecurityMetricInput(
        metric_name="pb",
        as_of_date=market.as_of_date,
        value=market.price / bvps.value,
        unit="multiple",
        metadata=_metadata(
            "market_price_divided_by_book_value_per_share",
            [bvps.source_id, market.source_id],
            financial_period_end=bvps.period_end.isoformat(),
        ),
    )


def _market_cap_metric(
    by_name: dict[str, list[MetricFinancialFact]], market: MetricMarketClose
) -> SecurityMetricInput | None:
    shares = _latest(by_name, "shares_outstanding")
    if shares is None or shares.value <= 0:
        return None
    return SecurityMetricInput(
        metric_name="market_cap",
        as_of_date=market.as_of_date,
        value=market.price * shares.value,
        unit="INR",
        metadata=_metadata(
            "market_price_times_shares_outstanding",
            [shares.source_id, market.source_id],
            financial_period_end=shares.period_end.isoformat(),
        ),
    )


def _metric(
    name: str,
    as_of_date: date,
    value: Decimal,
    unit: str | None,
    formula: str,
    facts: list[MetricFinancialFact],
    **extra: object,
) -> SecurityMetricInput:
    return SecurityMetricInput(
        metric_name=name,
        as_of_date=as_of_date,
        value=value,
        unit=unit,
        metadata=_metadata(formula, [fact.source_id for fact in facts], **extra),
    )


def _metadata(formula: str, source_ids: list[UUID], **extra: object) -> dict[str, object]:
    return {
        "derived": True,
        "calculation_version": 1,
        "formula": formula,
        "upstream_source_ids": sorted({str(value) for value in source_ids}),
        **extra,
    }


def _put(
    output: dict[str, SecurityMetricInput],
    source_ids: set[UUID],
    metric: SecurityMetricInput,
) -> None:
    if metric.metric_name not in PEER_USABLE_METRICS:
        return
    output[metric.metric_name] = metric
    source_ids.update(UUID(value) for value in metric.metadata["upstream_source_ids"])


def _latest(
    by_name: dict[str, list[MetricFinancialFact]], name: str
) -> MetricFinancialFact | None:
    rows = by_name.get(name, [])
    return rows[0] if rows else None


def _same_period_pair(
    by_name: dict[str, list[MetricFinancialFact]], left: str, right: str
) -> tuple[MetricFinancialFact, MetricFinancialFact] | None:
    right_by_period = {item.period_end: item for item in by_name.get(right, [])}
    for left_fact in by_name.get(left, []):
        right_fact = right_by_period.get(left_fact.period_end)
        if right_fact is not None:
            return left_fact, right_fact
    return None


def _at_or_before(
    rows: list[MetricFinancialFact], period_end: date
) -> MetricFinancialFact | None:
    return next((item for item in rows if item.period_end <= period_end), None)


def _comparable_previous(
    rows: list[MetricFinancialFact], latest: MetricFinancialFact
) -> MetricFinancialFact | None:
    candidates = [
        item
        for item in rows[1:]
        if item.period_type == latest.period_type and item.period_end < latest.period_end
    ]
    if latest.period_type in {"quarterly", "half_year", "nine_month"}:
        annual_comparable = [
            item
            for item in candidates
            if 270 <= (latest.period_end - item.period_end).days <= 460
        ]
        return annual_comparable[0] if annual_comparable else None
    return candidates[0] if candidates else None


def _units_compatible(*units: str | None) -> bool:
    normalized = {str(unit or "").strip().lower() for unit in units}
    normalized.discard("")
    return len(normalized) <= 1


def _percent_to_ratio(value: Decimal, unit: str | None) -> Decimal:
    normalized = str(unit or "").strip().lower()
    if normalized in {"%", "percent", "percentage", "pct"} or abs(value) > Decimal("2"):
        return value / Decimal("100")
    return value
