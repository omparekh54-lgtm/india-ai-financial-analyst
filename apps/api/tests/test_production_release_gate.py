from pathlib import Path
from uuid import uuid4

import pytest

from scripts.run_production_release_gate import (
    RELEASE_STAGE_ORDER,
    _parse_output,
    build_release_commands,
)


def _commands(**overrides: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "python_executable": "/usr/bin/python",
        "scripts_dir": Path("/app/scripts"),
        "api_base_url": "https://api.example.com",
        "job_id": str(uuid4()),
        "min_nse_eq_securities": 1000,
        "load_requests": 60,
        "load_concurrency": 5,
        "allow_http_localhost": False,
    }
    values.update(overrides)
    return build_release_commands(**values)  # type: ignore[arg-type]


def test_release_gate_runs_every_required_stage_in_order() -> None:
    stages = _commands()

    assert tuple(stage.name for stage in stages) == RELEASE_STAGE_ORDER
    assert "run_production_preflight.py" in stages[0].command[1]
    assert "run_production_corpus_manifest.py" in stages[1].command[1]
    assert "run_real_company_acceptance.py" in stages[2].command[1]
    assert "run_provider_activation_gate.py" in stages[3].command[1]
    assert "run_operations_health_report.py" in stages[4].command[1]
    assert "run_commercial_launch_gate.py" in stages[5].command[1]
    assert "run_deployment_smoke.py" in stages[6].command[1]
    assert "--require-corpus-ready" in stages[6].command
    assert "verify_auth_isolation.py" in stages[7].command[1]
    assert "run_load_probe.py" in stages[8].command[1]


def test_release_gate_never_places_access_tokens_or_provider_secrets_on_cli() -> None:
    flattened = " ".join(part for stage in _commands() for part in stage.command)

    assert "DEPLOYMENT_SMOKE_ACCESS_TOKEN" not in flattened
    assert "OWNER_ACCESS_TOKEN" not in flattened
    assert "OTHER_ACCESS_TOKEN" not in flattened
    assert "LOAD_PROBE_ACCESS_TOKEN" not in flattened
    assert "TAVILY_API_KEY" not in flattened
    assert "GROQ_API_KEY" not in flattened
    assert "UPSTOX_CLIENT_SECRET" not in flattened


def test_release_gate_requires_https_except_explicit_localhost() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        _commands(api_base_url="http://example.com")

    stages = _commands(
        api_base_url="http://localhost:8000",
        allow_http_localhost=True,
    )
    assert "--allow-http-localhost" in stages[7].command


def test_release_gate_preserves_production_bounds() -> None:
    with pytest.raises(ValueError, match="min_nse_eq_securities"):
        _commands(min_nse_eq_securities=999)
    with pytest.raises(ValueError, match="load_requests"):
        _commands(load_requests=501)
    with pytest.raises(ValueError, match="load_concurrency"):
        _commands(load_concurrency=26)


def test_release_gate_parse_output_prefers_final_json() -> None:
    assert _parse_output('{"ready": true}') == {"ready": True}
    assert _parse_output('progress\n{"ready": false}') == {"ready": False}
    assert _parse_output("") is None
