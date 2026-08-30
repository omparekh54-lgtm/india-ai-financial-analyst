from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.bootstrap import (
    build_bootstrap_plan,
    parse_benchmark_spec,
    parse_financial_spec,
    parse_macro_spec,
    parse_market_spec,
    parse_metrics_spec,
)


def _plan(**overrides: object):
    values: dict[str, object] = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "skip_nse": False,
        "nse_file": Path("/data/EQUITY_L.csv"),
        "nse_url": None,
        "nse_min_rows": 1000,
        "financials": (),
        "financial_min_rows": 5,
        "markets": (),
        "market_interval": "1d",
        "market_timezone": "Asia/Kolkata",
        "market_min_rows": 30,
        "metrics": (),
        "metrics_min_rows": 3,
        "benchmarks": (),
        "benchmark_interval": "1d",
        "benchmark_timezone": "Asia/Kolkata",
        "benchmark_min_rows": 30,
        "macros": (),
        "macro_min_rows": 1,
        "dry_run": False,
        "run_official_feeds": False,
        "official_feed_limit": 4,
        "embed_evidence": False,
        "embedding_limit": None,
    }
    values.update(overrides)
    return build_bootstrap_plan(**values)  # type: ignore[arg-type]


def test_parse_benchmark_spec_requires_official_source() -> None:
    spec = parse_benchmark_spec(
        " nifty50 , /data/nifty.csv , https://www.niftyindices.com/reports/historical-data "
    )
    assert spec.code == "NIFTY50"
    assert spec.file == Path("/data/nifty.csv")
    assert spec.source_url == "https://www.niftyindices.com/reports/historical-data"

    with pytest.raises(ValueError, match="official NSE/NSE Indices domain"):
        parse_benchmark_spec(
            "NIFTY50,/data/nifty.csv,https://licensed.example/nifty/history"
        )


def test_parse_benchmark_spec_rejects_incomplete_value() -> None:
    with pytest.raises(ValueError, match="CODE,FILE,OFFICIAL_SOURCE_URL"):
        parse_benchmark_spec("NIFTY50,/data/nifty.csv")


def test_parse_financial_spec_preserves_provenance_uri() -> None:
    spec = parse_financial_spec(
        "RELIANCE,/data/reliance.csv,https://licensed.example/reliance/fy26"
    )
    assert spec.security == "RELIANCE"
    assert spec.file == Path("/data/reliance.csv")
    assert spec.source_uri == "https://licensed.example/reliance/fy26"


def test_parse_financial_spec_rejects_incomplete_or_synthetic_value() -> None:
    with pytest.raises(ValueError, match="SECURITY,FILE,SOURCE_URI"):
        parse_financial_spec("RELIANCE,/data/reliance.csv")
    with pytest.raises(ValueError, match="non-production"):
        parse_financial_spec(
            "RELIANCE,/data/reliance.csv,https://licensed.example/sample/reliance.csv"
        )


def test_parse_market_spec_normalizes_provider_and_preserves_source() -> None:
    spec = parse_market_spec(
        "RELIANCE,NSE,/data/reliance_prices.csv,https://licensed.example/reliance/prices"
    )
    assert spec.security == "RELIANCE"
    assert spec.provider == "nse"
    assert spec.file == Path("/data/reliance_prices.csv")
    assert spec.source_uri == "https://licensed.example/reliance/prices"


def test_parse_market_spec_rejects_incomplete_or_synthetic_provider() -> None:
    with pytest.raises(ValueError, match="SECURITY,PROVIDER,FILE,SOURCE_URI"):
        parse_market_spec("RELIANCE,nse,/data/reliance_prices.csv")
    with pytest.raises(ValueError, match="synthetic/mock/sample"):
        parse_market_spec(
            "RELIANCE,synthetic-provider,/data/reliance_prices.csv,"
            "https://licensed.example/reliance/prices"
        )


def test_parse_metrics_spec_preserves_provenance_uri() -> None:
    spec = parse_metrics_spec(
        "RELIANCE,/data/reliance_metrics.csv,https://licensed.example/reliance/metrics"
    )
    assert spec.security == "RELIANCE"
    assert spec.file == Path("/data/reliance_metrics.csv")
    assert spec.source_uri == "https://licensed.example/reliance/metrics"


def test_parse_metrics_spec_rejects_incomplete_value() -> None:
    with pytest.raises(ValueError, match="SECURITY,FILE,SOURCE_URI"):
        parse_metrics_spec("RELIANCE,/data/reliance_metrics.csv")


def test_parse_macro_spec_requires_official_rbi_or_nsdl_provenance() -> None:
    rbi = parse_macro_spec(
        "RBI,repo_rate,/data/repo.csv,https://rbi.org.in/Scripts/Statistics.aspx"
    )
    assert rbi.provider == "rbi"
    assert rbi.series_key == "repo_rate"
    assert rbi.file == Path("/data/repo.csv")

    nsdl = parse_macro_spec(
        "NSDL,/data/flows.csv,https://fpi.nsdl.co.in/web/Reports/Latest.aspx"
    )
    assert nsdl.provider == "nsdl"
    assert nsdl.series_key is None

    with pytest.raises(ValueError, match="approved official domain"):
        parse_macro_spec(
            "RBI,repo_rate,/data/repo.csv,https://licensed.example/rbi/repo.csv"
        )


def test_bootstrap_plan_has_deterministic_dependency_order() -> None:
    financials = (
        parse_financial_spec(
            "RELIANCE,/data/reliance.csv,https://licensed.example/reliance/fy26"
        ),
    )
    markets = (
        parse_market_spec(
            "RELIANCE,nse,/data/reliance_prices.csv,https://licensed.example/reliance/prices"
        ),
    )
    metrics = (
        parse_metrics_spec(
            "RELIANCE,/data/reliance_metrics.csv,https://licensed.example/reliance/metrics"
        ),
    )
    benchmarks = (
        parse_benchmark_spec(
            "NIFTY50,/data/nifty.csv,https://www.niftyindices.com/reports/historical-data"
        ),
        parse_benchmark_spec(
            "INDIAVIX,/data/vix.csv,https://www.nseindia.com/market-data/india-vix"
        ),
    )
    macros = (
        parse_macro_spec(
            "RBI,repo_rate,/data/repo.csv,https://rbi.org.in/Scripts/Statistics.aspx"
        ),
    )
    plan = _plan(
        financials=financials,
        markets=markets,
        metrics=metrics,
        benchmarks=benchmarks,
        macros=macros,
        run_official_feeds=True,
        embed_evidence=True,
        embedding_limit=500,
    )

    assert [stage.name for stage in plan] == [
        "nse_security_master",
        "financial_1_RELIANCE",
        "market_1_RELIANCE",
        "metrics_1_RELIANCE",
        "benchmark_1_NIFTY50",
        "benchmark_2_INDIAVIX",
        "macro_1_RBI_repo_rate",
        "official_feeds",
        "evidence_embeddings",
    ]
    assert plan[-1].command[-2:] == ("--limit", "500")
    assert "import_official_benchmark_file.py" in plan[4].command[1]
    assert "import_official_macro_file.py" in plan[6].command[1]


def test_dry_run_propagates_only_to_file_validation_stages() -> None:
    plan = _plan(
        dry_run=True,
        financials=(
            parse_financial_spec(
                "RELIANCE,/data/reliance.csv,https://licensed.example/reliance/fy26"
            ),
        ),
        markets=(
            parse_market_spec(
                "RELIANCE,nse,/data/reliance_prices.csv,https://licensed.example/reliance/prices"
            ),
        ),
        metrics=(
            parse_metrics_spec(
                "RELIANCE,/data/reliance_metrics.csv,https://licensed.example/reliance/metrics"
            ),
        ),
        benchmarks=(
            parse_benchmark_spec(
                "NIFTY50,/data/nifty.csv,https://www.niftyindices.com/reports/historical-data"
            ),
        ),
        macros=(
            parse_macro_spec(
                "NSDL,/data/flows.csv,https://fpi.nsdl.co.in/web/Reports/Latest.aspx"
            ),
        ),
    )
    assert all(stage.command[-1] == "--dry-run" for stage in plan)


def test_dry_run_rejects_mutating_worker_stages() -> None:
    with pytest.raises(ValueError, match="dry-run cannot execute official feeds"):
        _plan(dry_run=True, run_official_feeds=True)

    with pytest.raises(ValueError, match="dry-run cannot execute official feeds"):
        _plan(dry_run=True, embed_evidence=True)


def test_skip_nse_allows_resumable_partial_bootstrap() -> None:
    plan = _plan(
        skip_nse=True,
        markets=(
            parse_market_spec(
                "RELIANCE,nse,/data/reliance_prices.csv,https://licensed.example/reliance/prices"
            ),
        ),
        metrics=(
            parse_metrics_spec(
                "RELIANCE,/data/reliance_metrics.csv,https://licensed.example/reliance/metrics"
            ),
        ),
        benchmarks=(
            parse_benchmark_spec(
                "NIFTY50,/data/nifty.csv,https://www.niftyindices.com/reports/historical-data"
            ),
        ),
    )
    assert [stage.name for stage in plan] == [
        "market_1_RELIANCE",
        "metrics_1_RELIANCE",
        "benchmark_1_NIFTY50",
    ]


def test_bootstrap_plan_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="financial_min_rows"):
        _plan(financial_min_rows=0)

    with pytest.raises(ValueError, match="market_min_rows"):
        _plan(market_min_rows=0)

    with pytest.raises(ValueError, match="metrics_min_rows"):
        _plan(metrics_min_rows=0)

    with pytest.raises(ValueError, match="benchmark_min_rows"):
        _plan(benchmark_min_rows=1)

    with pytest.raises(ValueError, match="interval=1d"):
        _plan(benchmark_interval="1h")

    with pytest.raises(ValueError, match="official_feed_limit"):
        _plan(official_feed_limit=21)

    with pytest.raises(ValueError, match="embedding_limit"):
        _plan(embedding_limit=0)
