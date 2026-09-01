from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
STAGE_ORDER = ("market", "financials", "peer_metrics", "readiness")


@dataclass(frozen=True)
class ResearchCorpusStage:
    name: str
    command: tuple[str, ...]


def build_commands(
    *,
    python_executable: str,
    scripts_dir: Path,
    min_nse_eq_securities: int,
    market_history_batch_size: int,
    market_history_max_batches: int,
    financial_batch_size: int,
    financial_max_batches: int,
    peer_metric_batch_size: int,
    peer_metric_max_batches: int,
    repo_source_url: str | None = None,
    repo_date_column: str | None = None,
    repo_value_column: str | None = None,
    cpi_source_url: str | None = None,
    cpi_date_column: str | None = None,
    cpi_value_column: str | None = None,
    iip_source_url: str | None = None,
    iip_date_column: str | None = None,
    iip_value_column: str | None = None,
    start_at: str = "market",
) -> tuple[ResearchCorpusStage, ...]:
    if min_nse_eq_securities < 1000:
        raise ValueError("min_nse_eq_securities must be >= 1000 for production")
    if not 1 <= market_history_batch_size <= 500:
        raise ValueError("market_history_batch_size must be between 1 and 500")
    if not 1 <= market_history_max_batches <= 100:
        raise ValueError("market_history_max_batches must be between 1 and 100")
    if not 1 <= financial_batch_size <= 100:
        raise ValueError("financial_batch_size must be between 1 and 100")
    if not 1 <= financial_max_batches <= 200:
        raise ValueError("financial_max_batches must be between 1 and 200")
    if not 1 <= peer_metric_batch_size <= 250:
        raise ValueError("peer_metric_batch_size must be between 1 and 250")
    if not 1 <= peer_metric_max_batches <= 200:
        raise ValueError("peer_metric_max_batches must be between 1 and 200")
    if start_at not in STAGE_ORDER:
        raise ValueError("start_at must be one of: " + ", ".join(STAGE_ORDER))

    if start_at == "market":
        for name, value in (
            ("repo_source_url", repo_source_url),
            ("cpi_source_url", cpi_source_url),
            ("iip_source_url", iip_source_url),
        ):
            if not value or not value.strip():
                raise ValueError(f"{name} is required when the market corpus stage will run")

    market_command = [
        python_executable,
        str(scripts_dir / "bootstrap_production_market_corpus.py"),
        "--min-rows",
        str(min_nse_eq_securities),
        "--classification-min-coverage-pct",
        "100.0",
        "--mapping-min-coverage-pct",
        "100.0",
        "--history-batch-size",
        str(market_history_batch_size),
        "--history-max-batches",
        str(market_history_max_batches),
    ]
    for flag, value in (
        ("--repo-source-url", repo_source_url),
        ("--repo-date-column", repo_date_column),
        ("--repo-value-column", repo_value_column),
        ("--cpi-source-url", cpi_source_url),
        ("--cpi-date-column", cpi_date_column),
        ("--cpi-value-column", cpi_value_column),
        ("--iip-source-url", iip_source_url),
        ("--iip-date-column", iip_date_column),
        ("--iip-value-column", iip_value_column),
    ):
        if value:
            market_command.extend([flag, value])

    stages = (
        ResearchCorpusStage("market", tuple(market_command)),
        ResearchCorpusStage(
            "financials",
            (
                python_executable,
                str(scripts_dir / "bootstrap_nse_financial_results_all.py"),
                "--batch-size",
                str(financial_batch_size),
                "--max-batches",
                str(financial_max_batches),
                "--min-selected-periods",
                "0",
            ),
        ),
        ResearchCorpusStage(
            "peer_metrics",
            (
                python_executable,
                str(scripts_dir / "bootstrap_derived_security_metrics_all.py"),
                "--batch-size",
                str(peer_metric_batch_size),
                "--max-batches",
                str(peer_metric_max_batches),
            ),
        ),
        ResearchCorpusStage(
            "readiness",
            (
                python_executable,
                str(scripts_dir / "run_agent_readiness_gate.py"),
                "--min-nse-eq-securities",
                str(min_nse_eq_securities),
            ),
        ),
    )
    return stages[STAGE_ORDER.index(start_at) :]


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


def _run_stage(stage: ResearchCorpusStage) -> dict[str, Any]:
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
            "Build and validate the complete production research corpus in fail-closed order: "
            "market/universe context, official NSE financial evidence, deterministic peer metrics, "
            "then the authoritative 16-agent readiness gate. No synthetic fallback is permitted."
        )
    )
    parser.add_argument("--min-nse-eq-securities", type=int, default=1000)
    parser.add_argument("--market-history-batch-size", type=int, default=250)
    parser.add_argument("--market-history-max-batches", type=int, default=20)
    parser.add_argument("--financial-batch-size", type=int, default=25)
    parser.add_argument("--financial-max-batches", type=int, default=100)
    parser.add_argument("--peer-metric-batch-size", type=int, default=100)
    parser.add_argument("--peer-metric-max-batches", type=int, default=100)
    parser.add_argument("--repo-source-url")
    parser.add_argument("--repo-date-column")
    parser.add_argument("--repo-value-column")
    parser.add_argument("--cpi-source-url")
    parser.add_argument("--cpi-date-column")
    parser.add_argument("--cpi-value-column")
    parser.add_argument("--iip-source-url")
    parser.add_argument("--iip-date-column")
    parser.add_argument("--iip-value-column")
    parser.add_argument("--start-at", choices=STAGE_ORDER, default="market")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    try:
        stages = build_commands(
            python_executable=sys.executable,
            scripts_dir=SCRIPTS_DIR,
            min_nse_eq_securities=args.min_nse_eq_securities,
            market_history_batch_size=args.market_history_batch_size,
            market_history_max_batches=args.market_history_max_batches,
            financial_batch_size=args.financial_batch_size,
            financial_max_batches=args.financial_max_batches,
            peer_metric_batch_size=args.peer_metric_batch_size,
            peer_metric_max_batches=args.peer_metric_max_batches,
            repo_source_url=args.repo_source_url,
            repo_date_column=args.repo_date_column,
            repo_value_column=args.repo_value_column,
            cpi_source_url=args.cpi_source_url,
            cpi_date_column=args.cpi_date_column,
            cpi_value_column=args.cpi_value_column,
            iip_source_url=args.iip_source_url,
            iip_date_column=args.iip_date_column,
            iip_value_column=args.iip_value_column,
            start_at=args.start_at,
        )
    except ValueError as exc:
        parser.error(str(exc))

    summary: dict[str, object] = {
        "status": "planned" if args.plan_only else "running",
        "data_policy": "real_source_linked_data_only_no_synthetic_fallback",
        "start_at": args.start_at,
        "stage_order": [stage.name for stage in stages],
    }
    if args.plan_only:
        summary["commands"] = [
            {"name": stage.name, "command": list(stage.command)} for stage in stages
        ]
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    results: list[dict[str, Any]] = []
    for stage in stages:
        result = _run_stage(stage)
        results.append(result)
        summary["stages"] = results
        if not result["ok"]:
            summary["status"] = "failed"
            summary["failed_stage"] = stage.name
            print(json.dumps(summary, indent=2, sort_keys=True, default=str))
            return 1

    summary["status"] = "completed"
    summary["production_research_corpus_ready"] = True
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
