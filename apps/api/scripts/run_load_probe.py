from __future__ import annotations

import argparse
import asyncio
import json
import os

import httpx

from app.core.load_probe import SAFE_LOAD_PROBE_ENDPOINTS, run_read_only_load_probe

_DEFAULT_ENDPOINTS = ("/health", "/ready", "/v1/system/agents")


async def _run(
    *,
    api_base_url: str,
    endpoints: tuple[str, ...],
    request_count: int,
    concurrency: int,
    timeout_seconds: float,
) -> int:
    access_token = os.getenv("LOAD_PROBE_ACCESS_TOKEN", "").strip() or None
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        report = await run_read_only_load_probe(
            client,
            api_base_url=api_base_url,
            endpoints=endpoints,
            request_count=request_count,
            concurrency=concurrency,
            access_token=access_token,
        )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bounded GET-only API load probe. The command cannot create research jobs, "
            "mutate Supabase, or call provider-backed research endpoints."
        )
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("API_BASE_URL", ""),
        help="Deployed API origin. Defaults to API_BASE_URL.",
    )
    parser.add_argument(
        "--endpoint",
        action="append",
        choices=sorted(SAFE_LOAD_PROBE_ENDPOINTS),
        help="Repeat to select safe GET endpoints. Defaults to health/readiness/agents.",
    )
    parser.add_argument("--requests", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args()

    if not args.api_base_url.strip():
        parser.error("--api-base-url or API_BASE_URL is required")
    if not 1 <= args.requests <= 500:
        parser.error("--requests must be between 1 and 500")
    if not 1 <= args.concurrency <= 25:
        parser.error("--concurrency must be between 1 and 25")
    if args.timeout_seconds <= 0 or args.timeout_seconds > 60:
        parser.error("--timeout-seconds must be greater than 0 and at most 60")

    endpoints = tuple(args.endpoint) if args.endpoint else _DEFAULT_ENDPOINTS
    try:
        return asyncio.run(
            _run(
                api_base_url=args.api_base_url,
                endpoints=endpoints,
                request_count=args.requests,
                concurrency=args.concurrency,
                timeout_seconds=args.timeout_seconds,
            )
        )
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
