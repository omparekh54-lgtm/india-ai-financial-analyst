from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from app.core.post_launch_acceptance import (
    evaluate_post_launch_evidence,
    required_post_launch_evidence_contract,
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("post-launch evidence file must contain a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fail-closed Phase 31-36 post-launch acceptance gate from "
            "externally collected production evidence."
        )
    )
    parser.add_argument(
        "--evidence-json",
        default=os.getenv("POST_LAUNCH_EVIDENCE_JSON", ""),
        help="Path to a JSON evidence file. Secrets must not be included.",
    )
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    if args.plan_only:
        print(json.dumps(required_post_launch_evidence_contract(), indent=2, sort_keys=True))
        return 0

    if not args.evidence_json.strip():
        print(
            json.dumps(
                {
                    "ready": False,
                    "status": "failed",
                    "error": "POST_LAUNCH_EVIDENCE_JSON or --evidence-json is required",
                    "contract": required_post_launch_evidence_contract(),
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

    report = evaluate_post_launch_evidence(evidence)
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
