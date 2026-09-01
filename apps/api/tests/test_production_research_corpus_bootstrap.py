from pathlib import Path

import pytest

from scripts.bootstrap_production_research_corpus import STAGE_ORDER, _parse_output, build_commands


def _commands(**overrides: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "min_nse_eq_securities": 1000,
        "market_history_batch_size": 250,
        "market_history_max_batches": 20,
        "financial_batch_size": 25,
        "financial_max_batches": 100,
        "peer_metric_batch_size": 100,
        "peer_metric_max_batches": 100,
        "start_at": "market",
    }
    values.update(overrides)
    return build_commands(**values)  # type: ignore[arg-type]


def test_research_corpus_stage_order_is_fail_closed() -> None:
    stages = _commands()

    assert tuple(stage.name for stage in stages) == STAGE_ORDER
    assert "bootstrap_production_market_corpus.py" in stages[0].command[1]
    assert "bootstrap_nse_financial_results_all.py" in stages[1].command[1]
    assert "bootstrap_derived_security_metrics_all.py" in stages[2].command[1]
    assert "run_agent_readiness_gate.py" in stages[3].command[1]


def test_research_corpus_can_resume_at_any_stage() -> None:
    stages = _commands(start_at="peer_metrics")
    assert [stage.name for stage in stages] == ["peer_metrics", "readiness"]


def test_research_corpus_preserves_production_thresholds() -> None:
    stages = _commands(min_nse_eq_securities=1200)
    market = stages[0].command
    readiness = stages[-1].command

    assert market[market.index("--min-rows") + 1] == "1200"
    assert market[market.index("--classification-min-coverage-pct") + 1] == "100.0"
    assert market[market.index("--mapping-min-coverage-pct") + 1] == "100.0"
    assert readiness[readiness.index("--min-nse-eq-securities") + 1] == "1200"


def test_research_corpus_rejects_nonproduction_or_invalid_limits() -> None:
    with pytest.raises(ValueError, match="min_nse_eq_securities"):
        _commands(min_nse_eq_securities=999)
    with pytest.raises(ValueError, match="market_history_batch_size"):
        _commands(market_history_batch_size=0)
    with pytest.raises(ValueError, match="financial_batch_size"):
        _commands(financial_batch_size=101)
    with pytest.raises(ValueError, match="peer_metric_batch_size"):
        _commands(peer_metric_batch_size=251)
    with pytest.raises(ValueError, match="start_at"):
        _commands(start_at="unknown")


def test_research_corpus_parse_output_prefers_final_json() -> None:
    assert _parse_output('{"ready": true}') == {"ready": True}
    assert _parse_output('progress\n{"ready": false}') == {"ready": False}
    assert _parse_output("") is None
    assert _parse_output("diagnostic") == {"stdout_tail": "diagnostic"}
