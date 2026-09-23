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


def test_deployment_cutover_workflow_is_manual_fail_closed_and_secret_safe() -> None:
    workflow = _workflow("deployment-cutover.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["deployment-cutover"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"

    text = (WORKFLOWS / "deployment-cutover.yml").read_text(encoding="utf-8")
    assert "RUN_DEPLOYMENT_CUTOVER" in text
    assert "run_deployment_cutover_gate.py" in text
    assert "DEPLOYMENT_CUTOVER_EVIDENCE_JSON" in text
    assert "deployment-cutover-evidence.json" in text
    assert "workflow_dispatch" in text
    assert "secrets." not in text
    assert "--api-key" not in text
    assert "--token" not in text
    assert "--client-secret" not in text


def test_production_promotion_workflow_is_manual_fail_closed_and_secret_safe() -> None:
    workflow = _workflow("production-promotion.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["production-promotion"]
    assert isinstance(job, dict)
    assert job["environment"] == "production"

    text = (WORKFLOWS / "production-promotion.yml").read_text(encoding="utf-8")
    assert "RUN_PRODUCTION_PROMOTION" in text
    assert "run_production_promotion_gate.py" in text
    assert "PRODUCTION_PROMOTION_EVIDENCE_JSON" in text
    assert "production-promotion-evidence.json" in text
    assert "workflow_dispatch" in text
    assert "secrets." not in text
    assert "--api-key" not in text
    assert "--token" not in text
    assert "--client-secret" not in text


def test_live_market_worker_exits_cleanly_when_feature_is_disabled() -> None:
    text = (REPO_ROOT / "apps/api/scripts/run_live_market_worker.py").read_text(
        encoding="utf-8",
    )

    assert "Live market worker is disabled" in text
    assert "return 0" in text.split("if not settings.enable_live_market:", 1)[1].split(
        "if not settings.broker_token_encryption_key:",
        1,
    )[0]
    assert "raise RuntimeError(\"ENABLE_LIVE_MARKET must be true" not in text


def test_free_tier_data_jobs_are_bounded_and_do_not_enable_paid_services() -> None:
    workflow = _workflow("free-tier-data-jobs.yml")
    triggers = workflow["on"]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"push", "schedule", "workflow_dispatch"}
    assert triggers["push"] == {
        "branches": ["main"],
        "paths": [".github/run-markers/nifty50-financials"],
    }
    assert workflow["permissions"] == {"contents": "read"}

    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"market", "classification", "financials", "peer-metrics"}
    assert all(job["environment"] == "production" for job in jobs.values())

    text = (WORKFLOWS / "free-tier-data-jobs.yml").read_text(encoding="utf-8")
    assert 'FREE_ONLY: "true"' in text
    assert 'ENABLE_EXTERNAL_LLM_CALLS: "false"' in text
    assert "secrets.DATABASE_URL" in text
    assert "financial_batches must be 1-8" in text
    assert "--supported-only" in text
    market_steps = jobs["market"]["steps"]
    benchmark_index = next(
        index for index, step in enumerate(market_steps)
        if step.get("name") == "Refresh official NSE benchmarks"
    )
    assert market_steps[benchmark_index + 1]["run"] == (
        "python scripts/sync_india_vix_macro.py --max-age-days 7"
    )
    assert "--min-coverage-pct 25" in text
    assert "nifty50-classification" in text
    assert "nifty50-financials" in text
    assert "nifty50-peer-metrics" in text
    assert "backfill_nse_industry_classification.py" in text
    assert text.count("--nifty50") == 3
    assert "NIFTY 50 push marker requires an after-symbol checkpoint" in text
    assert "ARGS=(--nifty50 --limit 8 --max-periods 10" in text
    assert 'ARGS+=(--after-symbol "$START_AFTER")' in text


def test_api_dockerfile_excludes_optional_worker_dependencies() -> None:
    dockerfile = (REPO_ROOT / "apps/api/Dockerfile").read_text(encoding="utf-8")
    install_line = next(line for line in dockerfile.splitlines() if 'pip install "."' in line)
    assert "live_market" not in install_line
    assert "market_imports" not in install_line
    assert "embeddings" not in install_line
    assert "documents" not in install_line


def test_manual_workflows_have_non_cancelling_production_concurrency_locks() -> None:
    for filename, group in (
        ("production-corpus.yml", "production-research-corpus"),
        ("production-release-gate.yml", "production-release-gate"),
        ("post-launch-acceptance.yml", "post-launch-acceptance"),
        ("deployment-readiness.yml", "deployment-readiness"),
        ("production-activation-readiness.yml", "production-activation-readiness"),
        ("deployment-cutover.yml", "deployment-cutover"),
        ("production-promotion.yml", "production-promotion"),
    ):
        workflow = _workflow(filename)
        concurrency = workflow["concurrency"]
        assert isinstance(concurrency, dict)
        assert concurrency["group"] == group
        assert concurrency["cancel-in-progress"] == "false"


def test_nifty50_peer_metrics_batch_is_marker_triggered_and_source_linked() -> None:
    workflow = _workflow("nifty50-peer-metrics.yml")
    assert workflow["on"] == {
        "push": {
            "branches": ["main"],
            "paths": [".github/run-markers/nifty50-peer-metrics"],
        }
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "free-tier-production-data",
        "cancel-in-progress": "false",
    }
    job = workflow["jobs"]["first-financial-batch"]
    assert job["environment"] == "production"
    assert job["timeout-minutes"] == "45"
    text = (WORKFLOWS / "nifty50-peer-metrics.yml").read_text(encoding="utf-8")
    assert "secrets.DATABASE_URL" in text
    assert 'FREE_ONLY: "true"' in text
    assert 'ENABLE_EXTERNAL_LLM_CALLS: "false"' in text
    assert "batch: first-16-nifty50" in text
    assert "--nifty50 --refresh-all --limit 16 --min-metrics 3" in text


def test_initial_nifty50_market_history_is_bounded_and_research_only() -> None:
    workflow = _workflow("nifty50-market-history.yml")
    assert workflow["on"] == {
        "push": {
            "branches": ["main"],
            "paths": [".github/run-markers/nifty50-market-history"],
        }
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {
        "group": "free-tier-production-data",
        "cancel-in-progress": "false",
    }
    job = workflow["jobs"]["first-financial-batch"]
    assert job["environment"] == "production"
    assert job["timeout-minutes"] == "45"
    text = (WORKFLOWS / "nifty50-market-history.yml").read_text(encoding="utf-8")
    assert "secrets.DATABASE_URL" in text
    assert 'FREE_ONLY: "true"' in text
    assert 'ENABLE_EXTERNAL_LLM_CALLS: "false"' in text
    assert "batch: first-16-nifty50" in text
    assert text.count("--security ") == 16
    assert "--lookback-days 365 --interval 1d" in text
    assert "--confirm-yahoo-research-use" in text


def test_nifty50_market_batch_derives_metrics_after_bars_in_same_run() -> None:
    workflow = _workflow("nifty50-market-history.yml")
    steps = workflow["jobs"]["first-financial-batch"]["steps"]
    assert steps[-2]["name"] == "Extend sourced daily history for the first 16 NIFTY 50 equities"
    assert steps[-1]["name"] == "Derive sourced peer metrics after historical bars"
    assert "--confirm-yahoo-research-use" in steps[-2]["run"]
    assert "--nifty50 --refresh-all --limit 16 --min-metrics 3" in steps[-1]["run"]
