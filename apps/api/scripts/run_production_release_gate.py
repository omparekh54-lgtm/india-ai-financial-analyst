from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
RELEASE_STAGE_ORDER = (
    "preflight",
    "corpus_readiness",
    "deployment_smoke",
    "auth_isolation",
    "load_probe",
)


@dataclass(frozen=True)
class ReleaseStage:
    name: str
    command: tuple[str, ...]


def _validate_api_url(value: str, *, allow_http_localhost: bool) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and hostname:
        return candidate
    if allow_http_localhost and parsed.scheme == "http" and hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        return candidate
    raise ValueError("api_base_url must use HTTPS, except explicitly allowed localhost HTTP")


def build_release_commands(
    *,
    python_executable: str,
    scripts_dir: Path,
    api_base_url: str,
    job_id: str,
    min_nse_eq_securities: int = 1000,
    load_requests: int = 60,
    load_concurrency: int = 5,
    allow_http_localhost: bool = False,
) -> tuple[ReleaseStage, ...]:
    base_url = _validate_api_url(api_base_url, allow_http_localhost=allow_http_localhost)
    normalized_job_id = str(UUID(job_id))
    if min_nse_eq_securities < 1000:
        raise ValueError("min_nse_eq_securities must be >= 1000 for production release")
    if not 1 <= load_requests <= 500:
        raise ValueError("load_requests must be between 1 and 500")
    if not 1 <= load_concurrency <= 25:
        raise ValueError("load_concurrency must be between 1 and 25")

    auth_command = [
        python_executable,
        str(scripts_dir / "verify_auth_isolation.py"),
        "--api-base-url",
        base_url,
        "--job-id",
        normalized_job_id,
    ]
    if allow_http_localhost:
        auth_command.append("--allow-http-localhost")

    return (
        ReleaseStage(
            "preflight",
            (python_executable, str(scripts_dir / "run_production_preflight.py")),
        ),
        ReleaseStage(
            "corpus_readiness",
            (
                python_executable,
                str(scripts_dir / "run_agent_readiness_gate.py"),
                "--min-nse-eq-securities",
                str(min_nse_eq_securities),
            ),
        ),
        ReleaseStage(
            "deployment_smoke",
            (
                python_executable,
                str(scripts_dir / "run_deployment_smoke.py"),
                "--api-base-url",
                base_url,
                "--require-corpus-ready",
            ),
        ),
        ReleaseStage("auth_isolation", tuple(auth_command)),
        ReleaseStage(
            "load_probe",
            (
                python_executable,
                str(scripts_dir / "run_load_probe.py"),
                "--api-base-url",
                base_url,
                "--requests",
                str(load_requests),
                "--concurrency",
                str(load_concurrency),
            ),
        ),
    )


def _parse_output(stdout: str) -> object:
    value = stdout.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        for line in reversed(value.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"stdout_tail": value[-4000:]}


def _run_stage(stage: ReleaseStage) -> dict[str, Any]:
    completed = subprocess.run(
        stage.command,
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    result: dict[str, Any] = {
        "name": stage.name,
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "result": _parse_output(completed.stdout),
    }
    stderr = completed.stderr.strip()
    if stderr:
        result["stderr_tail"] = stderr[-4000:]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fail-closed production release gate: configuration/database preflight, "
            "authoritative 16-agent corpus readiness, authenticated GET-only deployment smoke, "
            "two-user ownership isolation, and a bounded read-only load probe."
        )
    )
    parser.add_argument("--api-base-url", default=os.getenv("API_BASE_URL", ""))
    parser.add_argument("--job-id", default=os.getenv("AUTH_ISOLATION_JOB_ID", ""))
    parser.add_argument("--min-nse-eq-securities", type=int, default=1000)
    parser.add_argument("--load-requests", type=int, default=60)
    parser.add_argument("--load-concurrency", type=int, default=5)
    parser.add_argument("--allow-http-localhost", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    if args.plan_only and (not args.api_base_url.strip() or not args.job_id.strip()):
        payload = {
            "status": "planned",
            "requires": [
                "API_BASE_URL or --api-base-url",
                "AUTH_ISOLATION_JOB_ID or --job-id",
                "DEPLOYMENT_SMOKE_ACCESS_TOKEN",
                "OWNER_ACCESS_TOKEN",
                "OTHER_ACCESS_TOKEN",
                "optional LOAD_PROBE_ACCESS_TOKEN for authenticated safe endpoints",
            ],
            "stage_order": list(RELEASE_STAGE_ORDER),
            "secrets_policy": "access_tokens_are_environment_only_and_never_cli_arguments",
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if not args.api_base_url.strip():
        parser.error("--api-base-url or API_BASE_URL is required")
    if not args.job_id.strip():
        parser.error("--job-id or AUTH_ISOLATION_JOB_ID is required")
    try:
        stages = build_release_commands(
            python_executable=sys.executable,
            scripts_dir=SCRIPTS_DIR,
            api_base_url=args.api_base_url,
            job_id=args.job_id,
            min_nse_eq_securities=args.min_nse_eq_securities,
            load_requests=args.load_requests,
            load_concurrency=args.load_concurrency,
            allow_http_localhost=args.allow_http_localhost,
        )
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))

    if args.plan_only:
        print(
            json.dumps(
                {
                    "status": "planned",
                    "stage_order": [stage.name for stage in stages],
                    "commands": [
                        {"name": stage.name, "command": list(stage.command)} for stage in stages
                    ],
                    "secrets_policy": "access_tokens_are_environment_only_and_never_cli_arguments",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    results: list[dict[str, Any]] = []
    for stage in stages:
        result = _run_stage(stage)
        results.append(result)
        if not result["ok"]:
            print(
                json.dumps(
                    {
                        "status": "failed",
                        "release_ready": False,
                        "failed_stage": stage.name,
                        "stages": results,
                    },
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )
            return 1

    print(
        json.dumps(
            {
                "status": "completed",
                "release_ready": True,
                "stage_order": list(RELEASE_STAGE_ORDER),
                "stages": results,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
