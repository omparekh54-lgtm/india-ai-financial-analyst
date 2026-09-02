from datetime import date
from decimal import Decimal
from uuid import UUID

from app.ingestion.derived_metrics import (
    MetricFinancialFact,
    MetricMarketClose,
    derive_peer_metrics,
)

S1 = UUID("11111111-1111-1111-1111-111111111111")
S2 = UUID("22222222-2222-2222-2222-222222222222")
S3 = UUID("33333333-3333-3333-3333-333333333333")
S4 = UUID("44444444-4444-4444-4444-444444444444")


def _fact(
    name: str,
    period_end: date,
    value: str,
    *,
    period_type: str = "annual",
    unit: str | None = "INR crore",
    source_id: UUID = S1,
) -> MetricFinancialFact:
    return MetricFinancialFact(
        fact_name=name,
        period_end=period_end,
        period_type=period_type,
        value=Decimal(value),
        unit=unit,
        source_id=source_id,
    )


def _metrics(bundle: object) -> dict[str, object]:
    return {item.metric_name: item for item in bundle.metrics}  # type: ignore[attr-defined]


def test_general_company_derives_growth_margin_roce_and_market_metrics() -> None:
    facts = [
        _fact("revenue", date(2026, 3, 31), "1200", source_id=S1),
        _fact("revenue", date(2025, 3, 31), "1000", source_id=S2),
        _fact("ebitda", date(2026, 3, 31), "240", source_id=S1),
        _fact("ebit", date(2026, 3, 31), "180", source_id=S1),
        _fact("total_assets", date(2026, 3, 31), "1500", source_id=S1),
        _fact("current_liabilities", date(2026, 3, 31), "300", source_id=S1),
        _fact("pat", date(2026, 3, 31), "120", source_id=S1),
        _fact("total_equity", date(2026, 3, 31), "600", source_id=S1),
        _fact("eps_basic", date(2026, 3, 31), "12", unit="INR/share", source_id=S1),
        _fact(
            "book_value_per_share",
            date(2026, 3, 31),
            "60",
            unit="INR/share",
            source_id=S1,
        ),
        _fact("shares_outstanding", date(2026, 3, 31), "10", unit="crore shares", source_id=S1),
    ]
    bundle = derive_peer_metrics(
        facts,
        market=MetricMarketClose(date(2026, 8, 28), Decimal(120), S3),
    )
    metrics = _metrics(bundle)

    assert Decimal(str(metrics["revenue_growth"].value)) == Decimal("0.2")  # type: ignore[attr-defined]
    assert Decimal(str(metrics["ebitda_margin"].value)) == Decimal("0.2")  # type: ignore[attr-defined]
    assert Decimal(str(metrics["roce"].value)) == Decimal("0.15")  # type: ignore[attr-defined]
    assert Decimal(str(metrics["roe"].value)) == Decimal("0.2")  # type: ignore[attr-defined]
    assert Decimal(str(metrics["roa"].value)) == Decimal("0.08")  # type: ignore[attr-defined]
    assert Decimal(str(metrics["pe"].value)) == Decimal(10)  # type: ignore[attr-defined]
    assert Decimal(str(metrics["pb"].value)) == Decimal(2)  # type: ignore[attr-defined]
    assert bundle.industry_comparable_count >= 3
    assert bundle.checksum
    assert set(bundle.upstream_source_ids) == {S1, S2, S3}


def test_quarterly_growth_requires_prior_year_comparable_not_previous_quarter() -> None:
    facts = [
        _fact(
            "revenue",
            date(2026, 6, 30),
            "120",
            period_type="quarterly",
            source_id=S1,
        ),
        _fact(
            "revenue",
            date(2026, 3, 31),
            "110",
            period_type="quarterly",
            source_id=S2,
        ),
        _fact(
            "revenue",
            date(2025, 6, 30),
            "100",
            period_type="quarterly",
            source_id=S3,
        ),
    ]

    metrics = _metrics(derive_peer_metrics(facts))

    assert Decimal(str(metrics["revenue_growth"].value)) == Decimal("0.2")  # type: ignore[attr-defined]
    assert metrics["revenue_growth"].metadata["comparison_period_end"] == "2025-06-30"  # type: ignore[attr-defined]


def test_financial_company_uses_reported_ratios_and_nii_growth() -> None:
    facts = [
        _fact("net_interest_income", date(2026, 3, 31), "120", source_id=S1),
        _fact("net_interest_income", date(2025, 3, 31), "100", source_id=S2),
        _fact("roe_pct", date(2026, 3, 31), "18", unit="%", source_id=S1),
        _fact("roa_pct", date(2026, 3, 31), "1.8", unit="%", source_id=S1),
        _fact("gross_npa_pct", date(2026, 3, 31), "2.4", unit="%", source_id=S1),
        _fact("net_npa_pct", date(2026, 3, 31), "0.6", unit="%", source_id=S1),
        _fact("nim_pct", date(2026, 3, 31), "3.5", unit="%", source_id=S1),
    ]

    metrics = _metrics(derive_peer_metrics(facts))

    assert Decimal(str(metrics["revenue_growth"].value)) == Decimal("0.2")  # type: ignore[attr-defined]
    assert Decimal(str(metrics["roe"].value)) == Decimal("0.18")  # type: ignore[attr-defined]
    assert Decimal(str(metrics["roa"].value)) == Decimal("0.018")  # type: ignore[attr-defined]
    assert "gross_npa_pct" in metrics
    assert "net_npa_pct" in metrics
    assert "nim_pct" in metrics


def test_insurer_can_reach_peer_metric_breadth_without_ebitda() -> None:
    facts = [
        _fact("gross_written_premium", date(2026, 3, 31), "110", source_id=S1),
        _fact("gross_written_premium", date(2025, 3, 31), "100", source_id=S2),
        _fact("vnb_margin_pct", date(2026, 3, 31), "24", unit="%", source_id=S1),
        _fact("solvency_ratio_pct", date(2026, 3, 31), "190", unit="%", source_id=S1),
        _fact("combined_ratio_pct", date(2026, 3, 31), "98", unit="%", source_id=S1),
    ]

    metrics = _metrics(derive_peer_metrics(facts))

    assert {"revenue_growth", "vnb_margin_pct", "solvency_ratio_pct"} <= set(metrics)


def test_market_multiple_fallbacks_use_pat_equity_and_shares() -> None:
    facts = [
        _fact("pat", date(2026, 3, 31), "100", source_id=S1),
        _fact("total_equity", date(2026, 3, 31), "500", source_id=S2),
        _fact("shares_outstanding", date(2026, 3, 31), "10", unit="crore shares", source_id=S2),
    ]
    metrics = _metrics(
        derive_peer_metrics(
            facts,
            market=MetricMarketClose(date(2026, 8, 28), Decimal(100), S3),
        )
    )

    assert Decimal(str(metrics["pe"].value)) == Decimal(10)  # type: ignore[attr-defined]
    assert Decimal(str(metrics["pb"].value)) == Decimal(2)  # type: ignore[attr-defined]


def test_incompatible_units_do_not_create_false_ratio_metrics() -> None:
    facts = [
        _fact("revenue", date(2026, 3, 31), "100", unit="INR crore", source_id=S1),
        _fact("revenue", date(2025, 3, 31), "90", unit="INR crore", source_id=S2),
        _fact("ebitda", date(2026, 3, 31), "20", unit="INR lakh", source_id=S3),
    ]

    metrics = _metrics(derive_peer_metrics(facts))

    assert "revenue_growth" in metrics
    assert "ebitda_margin" not in metrics


def test_market_multiples_are_not_created_from_quarterly_eps_or_nonpositive_price() -> None:
    facts = [
        _fact(
            "eps_basic",
            date(2026, 6, 30),
            "3",
            period_type="quarterly",
            unit="INR/share",
            source_id=S1,
        )
    ]

    metrics = _metrics(
        derive_peer_metrics(
            facts,
            market=MetricMarketClose(date(2026, 8, 28), Decimal(100), S4),
        )
    )
    assert "pe" not in metrics

    try:
        derive_peer_metrics(facts, market=MetricMarketClose(date(2026, 8, 28), Decimal(0), S4))
    except ValueError as exc:
        assert "positive" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("nonpositive market price should be rejected")
