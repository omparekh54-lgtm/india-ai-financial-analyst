from __future__ import annotations

import argparse
import asyncio
import json
import os
from urllib.parse import urlparse
from uuid import UUID

import httpx

from app.core.auth_isolation import verify_auth_isolation


async def _run(args: argparse.Namespace) -> int:
    base_url = _validate_api_base_url(
        args.api_base_url,
        allow_http_localhost=args.allow_http_localhost,
    )
    owner_token = _read_secret_env(args.owner_token_env)
    other_token = _read_secret_env(args.other_token_env)
    if owner_token == other_token:
        raise ValueError("owner and other access tokens must belong to different users")

    job_id = UUID(args.job_id)
    async with httpx.AsyncClient(timeout=args.timeout_seconds) as client:
        report = await verify_auth_isolation(
            client,
            api_base_url=base_url,
            job_id=job_id,
            owner_token=owner_token,
            other_token=other_token,
        )

    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.passed else 1


def _read_secret_env(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("token environment variable name cannot be empty")
    value = os.environ.get(normalized, "").strip()
    if not value:
        raise ValueError(f"required access-token environment variable is not set: {normalized}")
    return value


def _validate_api_base_url(value: str, *, allow_http_localhost: bool) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and hostname:
        return candidate
    if (
        allow_http_localhost
        and parsed.scheme == "http"
        and hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        return candidate
    raise ValueError(
        "API base URL must use HTTPS; HTTP is allowed only for localhost with "
        "--allow-http-localhost"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only two-user ownership smoke test against an existing real research job. "
            "Access tokens are read from environment variables and are never printed."
        )
    )
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--owner-token-env", default="OWNER_ACCESS_TOKEN")
    parser.add_argument("--other-token-env", default="OTHER_ACCESS_TOKEN")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--allow-http-localhost", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0 or args.timeout_seconds > 60:
        parser.error("--timeout-seconds must be greater than 0 and at most 60")

    try:
        return asyncio.run(_run(args))
    except (httpx.HTTPError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
