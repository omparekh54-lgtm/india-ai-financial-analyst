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
    assert "run_production_corpus_manifest.py" in text
    assert "production-corpus-manifest.json" in text


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


def test_post_launch_acceptance_workflow_is_manual_fail_closed_and_secret_safe() -> None:
    workflow = _workflow("post-launch-acceptance.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["post-launch-gate"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"

    text = (WORKFLOWS / "post-launch-acceptance.yml").read_text(encoding="utf-8")
    assert "RUN_POST_LAUNCH_GATE" in text
    assert "run_post_launch_acceptance_gate.py" in text
    assert "POST_LAUNCH_EVIDENCE_JSON" in text
    assert "post-launch-evidence.json" in text
    assert "workflow_dispatch" in text
    assert "secrets." not in text
    assert "--api-key" not in text
    assert "--token" not in text
    assert "--client-secret" not in text


def test_deployment_readiness_workflow_is_manual_fail_closed_and_secret_safe() -> None:
    workflow = _workflow("deployment-readiness.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["deployment-readiness"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"

    text = (WORKFLOWS / "deployment-readiness.yml").read_text(encoding="utf-8")
    assert "RUN_DEPLOYMENT_READINESS" in text
    assert "run_deployment_readiness_gate.py" in text
    assert "DEPLOYMENT_READINESS_EVIDENCE_JSON" in text
    assert "deployment-readiness-evidence.json" in text
    assert "workflow_dispatch" in text
    assert "secrets." not in text
    assert "--api-key" not in text
    assert "--token" not in text
    assert "--client-secret" not in text



def test_production_activation_readiness_workflow_is_manual_fail_closed_and_secret_safe() -> None:
    workflow = _workflow("production-activation-readiness.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["production-activation-readiness"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"

    text = (WORKFLOWS / "production-activation-readiness.yml").read_text(encoding="utf-8")
    assert "RUN_PRODUCTION_ACTIVATION" in text
    assert "run_production_activation_gate.py" in text
    assert "PRODUCTION_ACTIVATION_EVIDENCE_JSON" in text
    assert "production-activation-evidence.json" in text
    assert "workflow_dispatch" in text
    assert "secrets." not in text
    assert "--api-key" not in text
    assert "--token" not in text
    assert "--client-secret" not in text

def test_manual_workflows_have_non_cancelling_production_concurrency_locks() -> None:
    for filename, group in (
        ("production-corpus.yml", "production-research-corpus"),
        ("production-release-gate.yml", "production-release-gate"),
        ("post-launch-acceptance.yml", "post-launch-acceptance"),
        ("deployment-readiness.yml", "deployment-readiness"),
        ("production-activation-readiness.yml", "production-activation-readiness"),
    ):
        workflow = _workflow(filename)
        concurrency = workflow["concurrency"]
        assert isinstance(concurrency, dict)
        assert concurrency["group"] == group
        assert concurrency["cancel-in-progress"] == "false"
