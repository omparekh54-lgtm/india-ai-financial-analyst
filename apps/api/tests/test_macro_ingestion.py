from datetime import date
from decimal import Decimal

from app.ingestion.macro import (
    MacroObservation,
    canonical_macro_series_key,
    normalize_macro_observation,
)


def test_macro_aliases_normalize_india_series() -> None:
    assert canonical_macro_series_key("Policy Repo Rate") == "repo_rate"
    assert canonical_macro_series_key("USD/INR") == "usd_inr"
    assert canonical_macro_series_key("DII Net Investment") == "dii_cash_net_cr"


def test_macro_observation_normalizes_numeric_value() -> None:
    item = normalize_macro_observation(
        MacroObservation(
            series_key="FII Cash Net",
            observation_date=date(2026, 8, 28),
            value="-1,245.50",
            unit="INR cr",
        )
    )
    assert item.series_key == "fii_cash_net_cr"
    assert item.value == Decimal("-1245.50")
