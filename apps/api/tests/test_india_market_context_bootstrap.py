from pathlib import Path

import pytest

from scripts.bootstrap_india_market_context import build_commands


def _commands(**overrides: object):
    values: dict[str, object] = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "benchmark_min_rows": 30,
        "flow_max_age_days": 7,
        "vix_max_age_days": 7,
        "rbi_10y_max_age_days": 45,
        "repo_source_url": None,
        "repo_date_column": None,
        "repo_value_column": None,
        "cpi_source_url": None,
        "cpi_date_column": None,
        "cpi_value_column": None,
        "iip_source_url": None,
        "iip_date_column": None,
        "iip_value_column": None,
        "dry_run": False,
    }
    values.update(overrides)
    return build_commands(**values)  # type: ignore[arg-type]


def test_market_context_bootstrap_has_fail_fast_dependency_order() -> None:
    commands = _commands()

    assert [name for name, _ in commands] == [
        "fred_usdinr_brent",
        "nse_benchmarks",
        "india_vix_macro_sync",
        "nse_fii_dii_flows",
        "rbi_10y",
    ]
    assert "bootstrap_fred_macro.py" in commands[0][1][1]
    assert "backfill_nse_benchmarks.py" in commands[1][1][1]
    assert "sync_india_vix_macro.py" in commands[2][1][1]
    assert "backfill_nse_fii_dii.py" in commands[3][1][1]
    assert "backfill_rbi_10y.py" in commands[4][1][1]


def test_market_context_includes_explicit_official_rbi_series_before_market_context() -> None:
    commands = _commands(
        repo_source_url="https://statistics.rbi.org.in/repo.csv",
        repo_date_column="Date",
        repo_value_column="Repo",
        cpi_source_url="https://statistics.rbi.org.in/cpi.csv",
        iip_source_url="https://statistics.rbi.org.in/iip.csv",
    )

    assert [name for name, _ in commands[:4]] == [
        "fred_usdinr_brent",
        "rbi_repo_rate",
        "rbi_cpi_yoy",
        "rbi_iip_yoy",
    ]
    repo = commands[1][1]
    assert repo[repo.index("--series-key") + 1] == "repo_rate"
    assert repo[repo.index("--date-column") + 1] == "Date"
    assert repo[repo.index("--value-column") + 1] == "Repo"


def test_market_context_dry_run_skips_database_dependent_vix_sync() -> None:
    commands = _commands(dry_run=True)

    assert [name for name, _ in commands] == [
        "fred_usdinr_brent",
        "nse_benchmarks",
        "nse_fii_dii_flows",
        "rbi_10y",
    ]
    assert all(command[-1] == "--dry-run" for _, command in commands)


def test_market_context_bootstrap_propagates_freshness_limits() -> None:
    commands = _commands(
        benchmark_min_rows=60,
        flow_max_age_days=3,
        vix_max_age_days=2,
        rbi_10y_max_age_days=30,
    )

    assert commands[1][1][-2:] == ["--min-rows", "60"]
    assert commands[2][1][-2:] == ["--max-age-days", "2"]
    assert commands[3][1][-2:] == ["--max-age-days", "3"]
    assert commands[4][1][-2:] == ["--max-age-days", "30"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"benchmark_min_rows": 1}, "benchmark_min_rows"),
        ({"flow_max_age_days": -1}, "flow_max_age_days"),
        ({"vix_max_age_days": -1}, "vix_max_age_days"),
        ({"rbi_10y_max_age_days": -1}, "rbi_10y_max_age_days"),
    ],
)
def test_market_context_bootstrap_rejects_invalid_thresholds(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _commands(**overrides)
