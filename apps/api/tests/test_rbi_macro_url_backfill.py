import pytest

from scripts.backfill_rbi_macro_url import RBI_ALLOWED_DOMAINS, build_spec


def test_rbi_url_backfill_only_allows_official_rbi_domains() -> None:
    assert RBI_ALLOWED_DOMAINS == {"rbi.org.in", "statistics.rbi.org.in"}


def test_rbi_url_backfill_builds_explicit_series_spec() -> None:
    spec = build_spec(
        series_key="repo_rate",
        unit="percent",
        date_column="Date",
        value_column="Policy Repo Rate",
    )

    assert spec.series_key == "repo_rate"
    assert spec.unit == "percent"
    assert spec.date_column == "Date"
    assert spec.value_column == "Policy Repo Rate"


def test_rbi_url_backfill_rejects_nonapproved_series() -> None:
    with pytest.raises(ValueError, match="series_key"):
        build_spec(
            series_key="unapproved_macro",
            unit=None,
            date_column=None,
            value_column=None,
        )
