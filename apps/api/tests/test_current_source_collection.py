import asyncio
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

def test_exception_diagnostic_does_not_export_secret_message() -> None:
    with patch("subprocess.run") as runner:
        runner.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="SourceFetchError: private secret\n",
        )
        result = run_import(["importer"], timeout=1)
    assert result["result"]["error_type"] == "SourceFetchError"
    assert "private" not in str(result)
    assert "secret" not in str(result)


def test_repeated_source_failures_stop_with_resume_cursor(capsys) -> None:
    from scripts.collect_nifty50_current_sources import collect

    universe = SimpleNamespace(
        entries=[SimpleNamespace(symbol=f"S{i:02}") for i in range(50)],
    )
    failure = {"ok": False, "validation": {
        "ok": False, "result": {"results": [{"error_type": "SourceFetchError"}]},
    }, "import": None}
    with (
        patch("scripts.collect_nifty50_current_sources.NseSectoralIndexFetcher") as fetcher,
        patch("scripts.collect_nifty50_current_sources.collect_financial_symbol") as importer,
        patch("scripts.collect_nifty50_current_sources.asyncio.sleep", new=AsyncMock()),
        patch("scripts.collect_nifty50_current_sources.incomplete_financial_symbols",
              new=AsyncMock(side_effect=lambda symbols: symbols)),
    ):
        fetcher.return_value.fetch = AsyncMock(return_value=universe)
        importer.side_effect = lambda symbol, **kwargs: {"symbol": symbol, **failure}
        exit_code = asyncio.run(collect("financials", limit=50, after_symbol=None))
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert exit_code == 1
    assert importer.call_count == 3
    assert summary["processed_count"] == 3
    assert summary["unprocessed_count"] == 47
    assert summary["next_after_symbol"] == "S02"


def test_resume_retries_missing_earnings_or_filings_even_with_complete_financials() -> None:
    from scripts.collect_nifty50_current_sources import incomplete_financial_symbols

    coverage = [
        SimpleNamespace(financial_history_securities=1,
                        recent_filing_evidence_securities=1,
                        recent_earnings_evidence_securities=1),
        SimpleNamespace(financial_history_securities=0,
                        recent_filing_evidence_securities=1,
                        recent_earnings_evidence_securities=1),
        SimpleNamespace(financial_history_securities=1,
                        recent_filing_evidence_securities=0,
                        recent_earnings_evidence_securities=1),
        SimpleNamespace(financial_history_securities=1,
                        recent_filing_evidence_securities=1,
                        recent_earnings_evidence_securities=0),
    ]
    engine = SimpleNamespace(dispose=AsyncMock())
    with (
        patch("scripts.collect_nifty50_current_sources.get_settings",
              return_value=SimpleNamespace(database_url="configured")),
        patch("scripts.collect_nifty50_current_sources.create_database_engine",
              return_value=engine),
        patch("scripts.collect_nifty50_current_sources.resolve_security",
              new=AsyncMock(return_value=("id", "name"))),
        patch("scripts.collect_nifty50_current_sources.load_security_agent_coverage",
              new=AsyncMock(side_effect=[(item, "symbol") for item in coverage])),
    ):
        selected = asyncio.run(incomplete_financial_symbols(["DONE", "FIN", "FILE", "EARN"]))
    assert selected == ["FIN", "FILE", "EARN"]
    engine.dispose.assert_awaited_once()
