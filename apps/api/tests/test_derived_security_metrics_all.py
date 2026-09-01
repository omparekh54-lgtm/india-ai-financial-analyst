from pathlib import Path

import pytest

from scripts.bootstrap_derived_security_metrics_all import _parse_output, build_batch_command


def _command(**overrides: object) -> tuple[str, ...]:
    values: dict[str, object] = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "batch_size": 100,
        "after_symbol": None,
        "min_metrics": 3,
        "refresh_all": False,
        "dry_run": False,
    }
    values.update(overrides)
    return build_batch_command(**values)  # type: ignore[arg-type]


def test_peer_metric_batch_command_defaults_to_incomplete_universe() -> None:
    command = _command()

    assert "backfill_derived_security_metrics.py" in command[1]
    assert "--all" in command
    assert command[command.index("--limit") + 1] == "100"
    assert command[command.index("--min-metrics") + 1] == "3"
    assert "--after-symbol" not in command
    assert "--refresh-all" not in command


def test_peer_metric_batch_command_supports_cursor_refresh_and_dry_run() -> None:
    command = _command(after_symbol="RELIANCE", refresh_all=True, dry_run=True)

    assert command[command.index("--after-symbol") + 1] == "RELIANCE"
    assert "--refresh-all" in command
    assert command[-1] == "--dry-run"


def test_peer_metric_batch_command_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        _command(batch_size=0)
    with pytest.raises(ValueError, match="batch_size"):
        _command(batch_size=251)
    with pytest.raises(ValueError, match="min_metrics"):
        _command(min_metrics=0)
    with pytest.raises(ValueError, match="min_metrics"):
        _command(min_metrics=7)


def test_peer_metric_parse_output_accepts_final_json_line() -> None:
    assert _parse_output('{"target_count": 0}') == {"target_count": 0}
    assert _parse_output('progress\n{"target_count": 100}') == {"target_count": 100}
    assert _parse_output("") is None
    assert _parse_output("diagnostic") == {"stdout_tail": "diagnostic"}
