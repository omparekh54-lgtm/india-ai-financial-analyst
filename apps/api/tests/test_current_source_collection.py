import subprocess
from unittest.mock import patch

from scripts.collect_nifty50_current_sources import collect_financial_symbol, run_import


def test_failed_validation_prevents_writes() -> None:
    with patch("scripts.collect_nifty50_current_sources.run_import") as importer:
        importer.return_value = {"ok": False, "result": {"failure_count": 1}}
        result = collect_financial_symbol("RELIANCE")
    assert importer.call_count == 1
    assert "--dry-run" in importer.call_args.args[0]
    assert result["import"] is None
    assert result["ok"] is False


def test_import_failure_remains_visible_after_successful_validation() -> None:
    with patch("scripts.collect_nifty50_current_sources.run_import") as importer:
        importer.side_effect = [{"ok": True}, {"ok": False}]
        result = collect_financial_symbol("RELIANCE")
    assert importer.call_count == 2
    assert "--dry-run" not in importer.call_args_list[1].args[0]
    assert result["ok"] is False


def test_connection_tracebacks_are_not_exported() -> None:
    with patch("subprocess.run") as runner:
        runner.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="connection secret traceback",
        )
        result = run_import(["scripts/backfill_nse_financial_results.py"], timeout=1)
    assert result["ok"] is False
    assert "secret" not in str(result)


def test_timeout_is_reported_without_traceback() -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("import", 1)):
        result = run_import(["scripts/backfill_nse_financial_results.py"], timeout=1)
    assert result == {"ok": False, "reason": "import_timeout", "timeout_seconds": 1}
