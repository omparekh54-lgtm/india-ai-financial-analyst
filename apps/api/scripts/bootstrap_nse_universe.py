from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_UPSTOX_APPROVAL_REFERENCE = "SG-2026-08-31-01"


def _parse_output(stdout: str) -> object:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"stdout_tail": text[-4000:]}


def _run(name: str, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    result: dict[str, Any] = {
        "name": name,
        "ok": completed.returncode == 0,
        "return_code": completed.returncode,
        "result": _parse_output(completed.stdout),
    }
    if completed.stderr.strip():
        result["stderr_tail"] = completed.stderr.strip()[-4000:]
    return result


def build_commands(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    if args.provider == "nse":
        if args.upstox_file or args.upstox_url:
            raise ValueError("Upstox source options require --provider upstox")
        master = [
            sys.executable,
            str(SCRIPTS_DIR / "import_nse_security_master.py"),
            "--min-rows",
            str(args.min_rows),
        ]
        if args.nse_file:
            master.extend(["--file", args.nse_file])
        elif args.nse_url:
            master.extend(["--url", args.nse_url])
    else:
        if args.nse_file or args.nse_url:
            raise ValueError("NSE source options require --provider nse")
        master = [
            sys.executable,
            str(SCRIPTS_DIR / "import_upstox_security_master.py"),
            "--min-rows",
            str(args.min_rows),
            "--approval-reference",
            args.upstox_approval_reference,
        ]
        if args.upstox_file:
            master.extend(["--file", args.upstox_file])
        elif args.upstox_url:
            master.extend(["--url", args.upstox_url])

    if args.dry_run:
        master.append("--dry-run")
        return [(f"{args.provider}_security_master_validation", master)]

    classification = [
        sys.executable,
        str(SCRIPTS_DIR / "backfill_nse_industry_classification.py"),
        "--apply",
        "--delay-ms",
        str(args.classification_delay_ms),
        "--min-coverage-pct",
        str(args.classification_min_coverage_pct),
    ]
    if args.classification_refresh_all:
        classification.append("--refresh-all")
    if args.classification_limit:
        classification.extend(["--limit", str(args.classification_limit)])

    return [
        (f"{args.provider}_security_master", master),
        ("nse_industry_classification", classification),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bootstrap the genuine NSE EQ universe and then attach official four-level NSE "
            "industry classification with source provenance."
        )
    )
    parser.add_argument("--provider", choices=("nse", "upstox"), default="nse")
    nse_source = parser.add_mutually_exclusive_group()
    nse_source.add_argument("--nse-file")
    nse_source.add_argument("--nse-url")
    upstox_source = parser.add_mutually_exclusive_group()
    upstox_source.add_argument("--upstox-file")
    upstox_source.add_argument("--upstox-url")
    parser.add_argument(
        "--upstox-approval-reference",
        default=DEFAULT_UPSTOX_APPROVAL_REFERENCE,
    )
    parser.add_argument("--min-rows", type=int, default=1000)
    parser.add_argument("--classification-delay-ms", type=int, default=350)
    parser.add_argument("--classification-min-coverage-pct", type=float, default=100.0)
    parser.add_argument("--classification-refresh-all", action="store_true")
    parser.add_argument("--classification-limit", type=int, default=0)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate only the security-master artifact. Classification is intentionally skipped "
            "because a dry-run does not write the universe that would be classified."
        ),
    )
    args = parser.parse_args()

    if args.min_rows < 1000:
        parser.error("--min-rows must be >= 1000 for the production NSE universe")
    if args.classification_delay_ms < 100:
        parser.error("--classification-delay-ms must be >= 100")
    if not 0 < args.classification_min_coverage_pct <= 100:
        parser.error("--classification-min-coverage-pct must be > 0 and <= 100")
    if args.classification_limit < 0:
        parser.error("--classification-limit must be >= 0")
    if not args.dry_run and args.classification_limit:
        parser.error(
            "--classification-limit is allowed only for validation/development probes; "
            "production universe bootstrap requires complete classification coverage"
        )

    try:
        commands = build_commands(args)
    except ValueError as exc:
        parser.error(str(exc))

    summary: dict[str, object] = {
        "data_policy": "real_provenance_required",
        "provider": args.provider,
        "dry_run": args.dry_run,
        "minimum_nse_eq_rows": args.min_rows,
        "classification_min_coverage_pct": args.classification_min_coverage_pct,
        "stages": [],
    }
    stage_results: list[dict[str, Any]] = []
    for name, command in commands:
        result = _run(name, command)
        stage_results.append(result)
        summary["stages"] = stage_results
        if not result["ok"]:
            summary["failed_stage"] = name
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 1

    if args.dry_run:
        summary["classification"] = "skipped_in_dry_run"
    else:
        summary["classification"] = "official_nse_4_tier_applied"
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
