from datetime import date
from pathlib import Path

import pytest

from scripts.bootstrap_production_market_corpus import (
    STAGE_ORDER,
    _parse_output,
    build_commands,
)


def _commands(**overrides: object):
    values: dict[str, object] = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "security_master_provider": "nse",
        "nse_file": "/data/EQUITY_L.csv",
        "nse_url": None,
        "upstox_security_master_file": None,
        "upstox_security_master_url": None,
        "upstox_mapping_file": "/data/NSE.json.gz",
        "upstox_mapping_url": None,
        "min_rows": 1000,
        "classification_delay_ms": 350,
        "classification_min_coverage_pct": 100.0,
        "mapping_min_coverage_pct": 100.0,
        "approval_reference": "SG-2026-08-31-01",
        "benchmark_min_rows": 30,
        "flow_max_age_days": 7,
        "vix_max_age_days": 7,
        "rbi_10y_max_age_days": 45,
        "repo_source_url": "https://statistics.rbi.org.in/repo.csv",
        "repo_date_column": "Date",
        "repo_value_column": "Repo",
        "cpi_source_url": "https://statistics.rbi.org.in/cpi.csv",
        "cpi_date_column": None,
        "cpi_value_column": None,
        "iip_source_url": "https://statistics.rbi.org.in/iip.csv",
        "iip_date_column": None,
        "iip_value_column": None,
        "history_from_date": date(2025, 4, 18),
        "history_to_date": date(2026, 8, 31),
        "history_batch_size": 250,
        "history_max_batches": 20,
        "access_token_env": "UPSTOX_DATA_ACCESS_TOKEN",
        "history_request_delay_seconds": 0.15,
        "start_at": "universe",
    }
    values.update(overrides)
    return build_commands(**values)  # type: ignore[arg-type]


def test_full_market_corpus_stage_order_and_contracts() -> None:
    stages = _commands()

    assert tuple(stage.name for stage in stages) == STAGE_ORDER

    universe, mappings, market_context, market_history = stages
    assert "bootstrap_nse_universe.py" in universe.command[1]
    assert "--provider" in universe.command
    assert "nse" in universe.command
    assert "--nse-file" in universe.command
    assert "/data/EQUITY_L.csv" in universe.command
    assert "--classification-min-coverage-pct" in universe.command

    assert "backfill_upstox_instrument_mappings.py" in mappings.command[1]
    assert "--min-coverage-pct" in mappings.command
    assert "100.0" in mappings.command
    assert "--file" in mappings.command
    assert "/data/NSE.json.gz" in mappings.command
    assert "SG-2026-08-31-01" in mappings.command

    assert "bootstrap_india_market_context.py" in market_context.command[1]
    assert "--benchmark-min-rows" in market_context.command
    assert "--rbi-10y-max-age-days" in market_context.command
    assert market_context.command[market_context.command.index("--repo-source-url") + 1] == (
        "https://statistics.rbi.org.in/repo.csv"
    )
    assert market_context.command[market_context.command.index("--repo-date-column") + 1] == "Date"
    assert market_context.command[market_context.command.index("--repo-value-column") + 1] == "Repo"
    assert market_context.command[market_context.command.index("--cpi-source-url") + 1] == (
        "https://statistics.rbi.org.in/cpi.csv"
    )
    assert market_context.command[market_context.command.index("--iip-source-url") + 1] == (
        "https://statistics.rbi.org.in/iip.csv"
    )

    assert "bootstrap_upstox_market_history_all.py" in market_history.command[1]
    assert "--from-date" in market_history.command
    assert "2025-04-18" in market_history.command
    assert "--to-date" in market_history.command
    assert "2026-08-31" in market_history.command
    assert "--access-token-env" in market_history.command
    assert "UPSTOX_DATA_ACCESS_TOKEN" in market_history.command


def test_resume_start_at_returns_only_remaining_stages() -> None:
    stages = _commands(start_at="market_context")
    assert [stage.name for stage in stages] == ["market_context", "market_history"]

    history_only = _commands(
        start_at="market_history",
        repo_source_url=None,
        cpi_source_url=None,
        iip_source_url=None,
    )
    assert [stage.name for stage in history_only] == ["market_history"]


def test_market_context_sources_are_required_only_when_that_stage_will_run() -> None:
    with pytest.raises(ValueError, match="repo_source_url"):
        _commands(repo_source_url=None)
    with pytest.raises(ValueError, match="cpi_source_url"):
        _commands(cpi_source_url=None)
    with pytest.raises(ValueError, match="iip_source_url"):
        _commands(iip_source_url=None)


def test_upstox_security_master_fallback_is_explicit_and_approved() -> None:
    stages = _commands(
        security_master_provider="upstox",
        nse_file=None,
        upstox_security_master_file="/data/upstox-master.json.gz",
    )

    universe = stages[0]
    assert "upstox" in universe.command
    assert "--upstox-file" in universe.command
    assert "/data/upstox-master.json.gz" in universe.command
    assert "--upstox-approval-reference" in universe.command
    assert "SG-2026-08-31-01" in universe.command


def test_source_options_cannot_cross_security_master_providers() -> None:
    with pytest.raises(ValueError, match="Upstox security-master options"):
        _commands(upstox_security_master_file="/data/upstox.json.gz")

    with pytest.raises(ValueError, match="NSE security-master options"):
        _commands(
            security_master_provider="upstox",
            upstox_security_master_file="/data/upstox.json.gz",
        )


def test_production_thresholds_and_history_window_fail_closed() -> None:
    with pytest.raises(ValueError, match="min_rows"):
        _commands(min_rows=999)

    with pytest.raises(ValueError, match="mapping_min_coverage_pct"):
        _commands(mapping_min_coverage_pct=0.0)

    with pytest.raises(ValueError, match="classification_min_coverage_pct"):
        _commands(classification_min_coverage_pct=100.1)

    with pytest.raises(ValueError, match="history_from_date"):
        _commands(
            history_from_date=date(2026, 9, 1),
            history_to_date=date(2026, 8, 31),
        )

    with pytest.raises(ValueError, match="history_batch_size"):
        _commands(history_batch_size=501)

    with pytest.raises(ValueError, match="history_max_batches"):
        _commands(history_max_batches=0)


def test_invalid_resume_stage_and_empty_operator_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="start_at"):
        _commands(start_at="financials")

    with pytest.raises(ValueError, match="approval_reference"):
        _commands(approval_reference=" ")

    with pytest.raises(ValueError, match="access_token_env"):
        _commands(access_token_env="")


def test_parse_output_handles_json_and_preserves_non_json_tail() -> None:
    assert _parse_output('{"ok": true}') == {"ok": True}
    assert _parse_output("progress\n{\"stage\": \"done\"}") == {"stage": "done"}
    assert _parse_output("") is None
    assert _parse_output("plain diagnostic") == {"stdout_tail": "plain diagnostic"}
