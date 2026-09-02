from pathlib import Path

import pytest

from scripts.bootstrap_nse_financial_results_all import (
    _coverage_pct,
    _parse_output,
    build_batch_command,
)


def _command(**overrides: object) -> tuple[str, ...]:
    values: dict[str, object] = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "batch_size": 25,
        "after_symbol": None,
        "max_periods": 10,
        "min_selected_periods": 0,
        "request_delay_seconds": 0.35,
        "document_delay_seconds": 0.10,
        "refresh_all": False,
        "dry_run": False,
    }
    values.update(overrides)
    return build_batch_command(**values)  # type: ignore[arg-type]


def test_batch_command_uses_listing_age_policy_by_default() -> None:
    command = _command()

    assert "backfill_nse_financial_results.py" in command[1]
    assert "--all" in command
    assert command[command.index("--limit") + 1] == "25"
    assert command[command.index("--max-periods") + 1] == "10"
    assert command[command.index("--min-selected-periods") + 1] == "0"
    assert "--after-symbol" not in command


def test_batch_command_supports_cursor_refresh_and_dry_run() -> None:
    command = _command(
        after_symbol="RELIANCE",
        refresh_all=True,
        dry_run=True,
        min_selected_periods=8,
    )

    assert command[command.index("--after-symbol") + 1] == "RELIANCE"
    assert command[command.index("--min-selected-periods") + 1] == "8"
    assert "--refresh-all" in command
    assert command[-1] == "--dry-run"


def test_batch_command_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        _command(batch_size=0)
    with pytest.raises(ValueError, match="batch_size"):
        _command(batch_size=101)
    with pytest.raises(ValueError, match="max_periods"):
        _command(max_periods=21)
    with pytest.raises(ValueError, match="min_selected_periods"):
        _command(min_selected_periods=11)
    with pytest.raises(ValueError, match="request_delay_seconds"):
        _command(request_delay_seconds=-0.1)
    with pytest.raises(ValueError, match="document_delay_seconds"):
        _command(document_delay_seconds=10.1)


def test_parse_output_accepts_final_json_line_and_diagnostics() -> None:
    assert _parse_output('{"target_count": 0}') == {"target_count": 0}
    assert _parse_output('progress\n{"target_count": 25}') == {"target_count": 25}
    assert _parse_output("") is None
    assert _parse_output("diagnostic only") == {"stdout_tail": "diagnostic only"}


def test_coverage_pct_handles_zero_and_rounding() -> None:
    assert _coverage_pct(0, 0) == 0.0
    assert _coverage_pct(1, 3) == 33.33
    assert _coverage_pct(3, 3) == 100.0
