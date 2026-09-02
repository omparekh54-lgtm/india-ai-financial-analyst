from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from app.core.production_promotion import (
    evaluate_production_promotion,
    required_production_promotion_contract,
)

_SECRET_KEY_MARKERS = (
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "bearer",
    "client_secret",
    "database_url",
    "password",
    "private_key",
    "refresh_token",
    "secret_key",
    "service_role_key",
    "service_role_secret",
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("production-promotion evidence file must contain a JSON object")
    secret_paths = _secret_like_paths(payload)
    if secret_paths:
        raise ValueError(
            "production-promotion evidence must not contain secret-like keys: "
            + ", ".join(secret_paths[:10])
        )
    return payload


def _secret_like_paths(value: object, prefix: str = "") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for raw_key, child in value.items():
            key = str(raw_key)
            path = f"{prefix}.{key}" if prefix else key
            normalized = key.lower().replace("-", "_")
            safe_absence_attestation = normalized.startswith("no_") and isinstance(child, bool)
            if (
                any(marker in normalized for marker in _SECRET_KEY_MARKERS)
                and not safe_absence_attestation
            ):
                paths.append(path)
            paths.extend(_secret_like_paths(child, path))
        return paths
    if isinstance(value, list):
        paths = []
        for index, child in enumerate(value):
            item_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            paths.extend(_secret_like_paths(child, item_prefix))
        return paths
    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fail-closed Phase 51-54 production-promotion gate from "
            "externally collected non-secret evidence."
        )
    )
    parser.add_argument(
        "--evidence-json",
        default=os.getenv("PRODUCTION_PROMOTION_EVIDENCE_JSON", ""),
        help="Path to a JSON evidence file. Secrets must not be included.",
    )
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    if args.plan_only:
        print(json.dumps(required_production_promotion_contract(), indent=2, sort_keys=True))
        return 0

    if not args.evidence_json.strip():
        print(
            json.dumps(
                {
                    "ready": False,
                    "status": "failed",
                    "error": "PRODUCTION_PROMOTION_EVIDENCE_JSON or --evidence-json is required",
                    "contract": required_production_promotion_contract(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    try:
        evidence = _load_json(Path(args.evidence_json))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "ready": False,
                    "status": "failed",
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    report = evaluate_production_promotion(evidence)
    print(
        json.dumps(
            {
                "ready": report.ready,
                "status": "completed" if report.ready else "failed",
                **report.as_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
