from datetime import date
from pathlib import Path

import pytest

from scripts.bootstrap_upstox_market_history_all import (
    _next_cursor,
    _parse_output,
    build_batch_command,
)


def test_build_batch_command_propagates_cursor_and_dry_run() -> None:
    command = build_batch_command(
        python_executable="/usr/bin/python",
        scripts_dir=Path("/app/scripts"),
        from_date=date(2025, 4, 1),
        to_date=date(2026, 8, 31),
        batch_size=250,
        after_symbol="abc",
        access_token_env="UPSTOX_DATA_ACCESS_TOKEN",
        approval_reference="SG-2026-08-31-01",
        request_delay_seconds=0.2,
        dry_run=True,
    )

    assert "backfill_upstox_market_history.py" in command[1]
    assert "--all" in command
    assert command[command.index("--after-symbol") + 1] == "ABC"
    assert command[command.index("--limit") + 1] == "250"
    assert command[-1] == "--dry-run"


def test_build_batch_command_rejects_invalid_ranges() -> None:
    base = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "from_date": date(2025, 4, 1),
        "to_date": date(2026, 8, 31),
        "batch_size": 250,
        "after_symbol": None,
        "access_token_env": "UPSTOX_DATA_ACCESS_TOKEN",
        "approval_reference": "SG-2026-08-31-01",
        "request_delay_seconds": 0.15,
        "dry_run": False,
    }
    with pytest.raises(ValueError, match="from_date"):
        build_batch_command(**{**base, "from_date": date(2026, 9, 1)})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="batch_size"):
        build_batch_command(**{**base, "batch_size": 501})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="request_delay_seconds"):
        build_batch_command(**{**base, "request_delay_seconds": -0.1})  # type: ignore[arg-type]


def test_parse_output_requires_json_object() -> None:
    assert _parse_output('{"target_count": 2}') == {"target_count": 2}
    with pytest.raises(ValueError, match="no JSON"):
        _parse_output(" ")
    with pytest.raises(ValueError, match="invalid JSON"):
        _parse_output("not-json")
    with pytest.raises(TypeError, match="JSON object"):
        _parse_output("[]")


def test_next_cursor_handles_live_and_dry_run_payloads() -> None:
    assert _next_cursor({"next_after_symbol": "reliance"}, dry_run=False) == "RELIANCE"
    assert _next_cursor({"next_after_symbol": None}, dry_run=False) is None
    assert (
        _next_cursor(
            {"targets": [{"symbol": "ABC"}, {"symbol": "XYZ"}]},
            dry_run=True,
        )
        == "XYZ"
    )
    assert _next_cursor({"targets": []}, dry_run=True) is None
