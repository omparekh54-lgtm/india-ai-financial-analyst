from datetime import date
from decimal import Decimal

from app.ingestion.metrics import (
    SecurityMetricInput,
    canonical_security_metric,
    normalize_security_metric,
)


def test_security_metric_aliases_normalize() -> None:
    assert canonical_security_metric("P/E") == "pe"
    assert canonical_security_metric("EV / EBITDA") == "ev_ebitda"
    assert canonical_security_metric("Return on Capital Employed") == "roce"


def test_security_metric_numeric_value_is_decimal() -> None:
    item = normalize_security_metric(
        SecurityMetricInput(
            metric_name="Price to Book",
            as_of_date=date(2026, 8, 28),
            value="3.75",
        )
    )
    assert item.metric_name == "pb"
    assert item.value == Decimal("3.75")
