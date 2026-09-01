from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _workflow(name: str) -> dict[str, object]:
    payload = yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(payload, dict)
    return payload


def test_production_corpus_workflow_is_manual_and_read_only_to_github() -> None:
    workflow = _workflow("production-corpus.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["build-corpus"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"
    assert job["timeout-minutes"] == "360"

    text = (WORKFLOWS / "production-corpus.yml").read_text(encoding="utf-8")
    assert "RUN_REAL_CORPUS" in text
    assert 'FREE_ONLY: "true"' in text
    assert 'ENABLE_EXTERNAL_LLM_CALLS: "false"' in text
    assert 'ENABLE_EVENT_RESEARCH: "false"' in text
    assert "secrets.DATABASE_URL" in text
    assert "secrets.FRED_API_KEY" in text
    assert "secrets.UPSTOX_DATA_ACCESS_TOKEN" in text


def test_production_release_workflow_is_manual_fail_closed_and_secret_safe() -> None:
    workflow = _workflow("production-release-gate.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["release-gate"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"

    text = (WORKFLOWS / "production-release-gate.yml").read_text(encoding="utf-8")
    assert "RUN_RELEASE_GATE" in text
    assert "run_production_release_gate.py" in text
    assert "REAL_COMPANY_ACCEPTANCE_JOB_IDS" in text
    assert 'COMMERCIAL_LAUNCH_ENABLED: "true"' in text
    assert "enable_external_llm_calls" in text
    assert "enable_external_data_calls" in text
    assert "enable_live_market" in text
    assert "DEPLOYMENT_SMOKE_ACCESS_TOKEN" in text
    assert "OWNER_ACCESS_TOKEN" in text
    assert "OTHER_ACCESS_TOKEN" in text
    assert "secrets.TAVILY_API_KEY" in text
    assert "secrets.UPSTOX_CLIENT_SECRET" in text

    command_section = text.split("Run production release gate", 1)[1]
    assert "--access-token" not in command_section
    assert "--owner-token" not in command_section
    assert "--other-token" not in command_section
    assert "--client-secret" not in command_section
    assert "--api-key" not in command_section


def test_manual_workflows_have_non_cancelling_production_concurrency_locks() -> None:
    for filename, group in (
        ("production-corpus.yml", "production-research-corpus"),
        ("production-release-gate.yml", "production-release-gate"),
    ):
        workflow = _workflow(filename)
        concurrency = workflow["concurrency"]
        assert isinstance(concurrency, dict)
        assert concurrency["group"] == group
        assert concurrency["cancel-in-progress"] == "false"
