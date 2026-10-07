"""Run the research worker and the daily shared-market refresh on one local machine.

Designed for the owner's own computer in India, which can reach NSE where cloud hosts are
blocked. The node needs no inbound network access: the research worker polls the Supabase
job queue, and the daily refresh writes straight to the same database.

What it runs:

* The durable research worker (one job at a time): fetch the requested stock's data on
  demand, apply the per-security readiness contract, run the 16 agents.
* Once per trading day after the NSE close (19:00 IST), the shared-market refresh used by
  every stock: daily prices for the supported universe, NIFTY 50 / India VIX benchmarks,
  and the India VIX macro sync. A refresh missed while the computer was off runs on the next
  start. Failed refreshes retry up to three times per session, an hour apart.

Usage::

    python scripts/run_local_node.py               # worker + daily refresh
    python scripts/run_local_node.py --refresh-now # run the shared refresh once, then continue
    python scripts/run_local_node.py --no-worker   # only the daily refresh loop
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

IST = ZoneInfo("Asia/Kolkata")
REFRESH_AFTER = time(19, 0)
MAX_ATTEMPTS_PER_SESSION = 3
RETRY_SPACING = timedelta(hours=1)
CHECK_INTERVAL_SECONDS = 600

log = logging.getLogger("local-node")


@dataclass(frozen=True)
class RefreshStep:
    name: str
    args: tuple[str, ...]
    timeout_seconds: int


# Every step is an existing, provenance-checked importer. Nothing here writes data directly.
DAILY_REFRESH_STEPS: tuple[RefreshStep, ...] = (
    RefreshStep(
        "prices",
        (
            "scripts/backfill_yfinance_market_history.py",
            "--all",
            "--from-listing",
            "--daily-refresh",
            "--supported-only",
            "--recent-history-days",
            "400",
            "--interval",
            "1d",
            "--limit",
            "10000",
            "--request-delay-seconds",
            "0.5",
            "--confirm-yahoo-research-use",
        ),
        timeout_seconds=150 * 60,
    ),
    RefreshStep(
        "benchmarks",
        ("scripts/backfill_nse_benchmarks.py", "--min-rows", "2"),
        timeout_seconds=15 * 60,
    ),
    RefreshStep(
        "india_vix_macro",
        ("scripts/sync_india_vix_macro.py", "--max-age-days", "7"),
        timeout_seconds=5 * 60,
    ),
)


def latest_due_session(now: datetime) -> date:
    """The most recent weekday whose post-close refresh time has passed (IST).

    Exchange holidays are not modelled: a refresh on a holiday finds no new bars and is a
    cheap, idempotent no-op in every importer.
    """
    local = now.astimezone(IST)
    day = local.date()
    if local.time() < REFRESH_AFTER:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


@dataclass
class RefreshState:
    last_success_session: date | None = None
    attempt_session: date | None = None
    attempts: int = 0
    last_attempt_at: datetime | None = None

    @classmethod
    def load(cls, path: Path) -> RefreshState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(
            last_success_session=_date_or_none(raw.get("last_success_session")),
            attempt_session=_date_or_none(raw.get("attempt_session")),
            attempts=int(raw.get("attempts") or 0),
            last_attempt_at=_datetime_or_none(raw.get("last_attempt_at")),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "last_success_session": _iso(self.last_success_session),
                    "attempt_session": _iso(self.attempt_session),
                    "attempts": self.attempts,
                    "last_attempt_at": _iso(self.last_attempt_at),
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def refresh_is_due(state: RefreshState, now: datetime) -> bool:
    session = latest_due_session(now)
    if state.last_success_session is not None and state.last_success_session >= session:
        return False
    if state.attempt_session == session:
        if state.attempts >= MAX_ATTEMPTS_PER_SESSION:
            return False
        if state.last_attempt_at is not None and now - state.last_attempt_at < RETRY_SPACING:
            return False
    return True


async def run_step(step: RefreshStep) -> dict[str, object]:
    started = datetime.now(UTC)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *step.args,
        cwd=str(API_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=step.timeout_seconds)
        status = "succeeded" if process.returncode == 0 else "failed"
    except TimeoutError:
        process.kill()
        output, _ = await process.communicate()
        status = "timed_out"
    tail = (output or b"").decode("utf-8", errors="replace").strip().splitlines()[-5:]
    return {
        "step": step.name,
        "status": status,
        "exit_code": process.returncode,
        "seconds": round((datetime.now(UTC) - started).total_seconds()),
        "output_tail": tail,
    }


async def run_daily_refresh(
    state: RefreshState,
    state_path: Path,
    *,
    steps: Sequence[RefreshStep] = DAILY_REFRESH_STEPS,
    now: datetime | None = None,
) -> bool:
    moment = now or datetime.now(UTC)
    session = latest_due_session(moment)
    if state.attempt_session != session:
        state.attempt_session = session
        state.attempts = 0
    state.attempts += 1
    state.last_attempt_at = moment
    state.save(state_path)

    results = []
    for step in steps:
        result = await run_step(step)
        results.append(result)
        log.info(json.dumps({"event": "daily_refresh_step", **result}))
    succeeded = all(result["status"] == "succeeded" for result in results)
    if succeeded:
        state.last_success_session = session
    state.save(state_path)
    log.info(
        json.dumps(
            {
                "event": "daily_refresh_finished",
                "session": session.isoformat(),
                "succeeded": succeeded,
                "attempt": state.attempts,
            }
        )
    )
    return succeeded


async def daily_refresh_loop(state_path: Path, *, refresh_now: bool) -> None:
    state = RefreshState.load(state_path)
    if refresh_now:
        await run_daily_refresh(state, state_path)
    while True:
        if refresh_is_due(state, datetime.now(UTC)):
            await run_daily_refresh(state, state_path)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def research_worker() -> None:
    from app.core.config import get_settings
    from app.db import create_database_engine
    from app.observability import configure_sentry
    from app.workers.research_jobs import ResearchJobWorker

    settings = get_settings()
    configure_sentry(settings, service="local-research-worker")
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for the research worker")
    if not settings.enable_external_data_calls:
        log.warning(
            "ENABLE_EXTERNAL_DATA_CALLS is false: research jobs will only use stored data "
            "and will not fetch a requested stock's missing data."
        )
    engine = create_database_engine(settings.database_url)
    try:
        await ResearchJobWorker(engine, settings).run_forever()
    finally:
        await engine.dispose()


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-worker", action="store_true", help="Do not run research jobs.")
    parser.add_argument(
        "--no-daily-refresh",
        action="store_true",
        help="Do not run the daily shared-market refresh.",
    )
    parser.add_argument(
        "--refresh-now",
        action="store_true",
        help="Run the shared-market refresh once at start-up.",
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get(
            "LOCAL_NODE_STATE_FILE",
            str(Path.home() / ".india-ai-analyst" / "local-node-state.json"),
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    tasks = []
    if not args.no_worker:
        tasks.append(asyncio.create_task(research_worker(), name="research-worker"))
    if not args.no_daily_refresh:
        tasks.append(
            asyncio.create_task(
                daily_refresh_loop(Path(args.state_file), refresh_now=args.refresh_now),
                name="daily-refresh",
            )
        )
    if not tasks:
        parser.error("nothing to run: both the worker and the daily refresh are disabled")
    log.info(json.dumps({"event": "local_node_started", "tasks": [t.get_name() for t in tasks]}))
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in pending:
        task.cancel()
    for task in done:
        task.result()
    return 0


def _date_or_none(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _datetime_or_none(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0) from None
