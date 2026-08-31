from __future__ import annotations

import argparse
import asyncio
import json
import os

import httpx

from app.core.deployment_smoke import verify_deployment_smoke


async def _run(*, api_base_url: str, require_corpus_ready: bool, timeout_seconds: float) -> int:
    access_token = os.getenv("DEPLOYMENT_SMOKE_ACCESS_TOKEN", "").strip()
    if not access_token:
        raise RuntimeError(
            "DEPLOYMENT_SMOKE_ACCESS_TOKEN must be provided through the environment; "
            "tokens are intentionally not accepted as CLI arguments"
        )

    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        report = await verify_deployment_smoke(
            client,
            api_base_url=api_base_url,
            access_token=access_token,
            require_corpus_ready=require_corpus_ready,
        )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run GET-only production smoke checks without creating research or mutating data."
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("API_BASE_URL", ""),
        help="Deployed API origin. Defaults to API_BASE_URL.",
    )
    parser.add_argument("--require-corpus-ready", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    args = parser.parse_args()

    if not args.api_base_url.strip():
        parser.error("--api-base-url or API_BASE_URL is required")
    if args.timeout_seconds <= 0 or args.timeout_seconds > 60:
        parser.error("--timeout-seconds must be greater than 0 and at most 60")

    try:
        return asyncio.run(
            _run(
                api_base_url=args.api_base_url,
                require_corpus_ready=args.require_corpus_ready,
                timeout_seconds=args.timeout_seconds,
            )
        )
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
