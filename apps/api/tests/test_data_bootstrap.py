from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.bootstrap import build_bootstrap_plan, parse_benchmark_spec


def _plan(**overrides: object):
    values: dict[str, object] = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "skip_nse": False,
        "nse_file": Path("/data/EQUITY_L.csv"),
        "nse_url": None,
        "nse_min_rows": 1000,
        "benchmarks": (),
        "benchmark_interval": "1d",
        "benchmark_timezone": "Asia/Kolkata",
        "benchmark_min_rows": 30,
        "macro_files": (),
        "macro_min_rows": 1,
        "dry_run": False,
        "run_official_feeds": False,
        "official_feed_limit": 4,
        "embed_evidence": False,
        "embedding_limit": None,
    }
    values.update(overrides)
    return build_bootstrap_plan(**values)  # type: ignore[arg-type]


def test_parse_benchmark_spec_normalizes_code_and_provider() -> None:
    spec = parse_benchmark_spec(" nifty50 , NSE , /data/nifty.csv ")
    assert spec.code == "NIFTY50"
    assert spec.provider == "nse"
    assert spec.file == Path("/data/nifty.csv")


def test_parse_benchmark_spec_rejects_incomplete_value() -> None:
    with pytest.raises(ValueError, match="CODE,PROVIDER,FILE"):
        parse_benchmark_spec("NIFTY50,/data/nifty.csv")


def test_bootstrap_plan_has_deterministic_dependency_order() -> None:
    benchmarks = (
        parse_benchmark_spec("NIFTY50,nse,/data/nifty.csv"),
        parse_benchmark_spec("INDIAVIX,nse,/data/vix.csv"),
    )
    plan = _plan(
        benchmarks=benchmarks,
        macro_files=(Path("/data/rbi.csv"),),
        run_official_feeds=True,
        embed_evidence=True,
        embedding_limit=500,
    )

    assert [stage.name for stage in plan] == [
        "nse_security_master",
        "benchmark_1_NIFTY50",
        "benchmark_2_INDIAVIX",
        "macro_1",
        "official_feeds",
        "evidence_embeddings",
    ]
    assert plan[-1].command[-2:] == ("--limit", "500")


def test_dry_run_propagates_only_to_file_validation_stages() -> None:
    plan = _plan(
        dry_run=True,
        benchmarks=(parse_benchmark_spec("NIFTY50,nse,/data/nifty.csv"),),
        macro_files=(Path("/data/rbi.csv"),),
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
        benchmarks=(parse_benchmark_spec("NIFTY50,nse,/data/nifty.csv"),),
    )
    assert [stage.name for stage in plan] == ["benchmark_1_NIFTY50"]


def test_bootstrap_plan_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="official_feed_limit"):
        _plan(official_feed_limit=21)

    with pytest.raises(ValueError, match="embedding_limit"):
        _plan(embedding_limit=0)
