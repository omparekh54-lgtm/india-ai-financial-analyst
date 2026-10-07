from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from scripts.run_local_node import (
    MAX_ATTEMPTS_PER_SESSION,
    RefreshState,
    RefreshStep,
    latest_due_session,
    refresh_is_due,
    run_daily_refresh,
)

IST = ZoneInfo("Asia/Kolkata")


def ist(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def test_due_session_is_today_only_after_the_post_close_time() -> None:
    # Wednesday 7 Oct 2026.
    assert latest_due_session(ist(2026, 10, 7, 18, 59)) == date(2026, 10, 6)
    assert latest_due_session(ist(2026, 10, 7, 19, 0)) == date(2026, 10, 7)


def test_weekends_map_back_to_friday() -> None:
    assert latest_due_session(ist(2026, 10, 10, 12)) == date(2026, 10, 9)  # Saturday
    assert latest_due_session(ist(2026, 10, 12, 9)) == date(2026, 10, 9)  # Monday morning


def test_refresh_missed_while_computer_was_off_runs_on_next_start() -> None:
    state = RefreshState(last_success_session=date(2026, 10, 5))
    assert refresh_is_due(state, ist(2026, 10, 7, 9))


def test_refresh_not_repeated_after_success() -> None:
    state = RefreshState(last_success_session=date(2026, 10, 7))
    assert not refresh_is_due(state, ist(2026, 10, 7, 23))


def test_failed_refresh_retries_after_spacing_then_gives_up() -> None:
    now = ist(2026, 10, 7, 20)
    state = RefreshState(
        last_success_session=date(2026, 10, 6),
        attempt_session=date(2026, 10, 7),
        attempts=1,
        last_attempt_at=now - timedelta(minutes=20),
    )
    assert not refresh_is_due(state, now)
    state.last_attempt_at = now - timedelta(hours=2)
    assert refresh_is_due(state, now)
    state.attempts = MAX_ATTEMPTS_PER_SESSION
    assert not refresh_is_due(state, now)


@pytest.mark.asyncio
async def test_session_marked_done_only_when_every_step_succeeds(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = RefreshState()
    now = ist(2026, 10, 7, 20)
    steps = (RefreshStep("a", ("x",), 1), RefreshStep("b", ("y",), 1))
    outcomes = iter(["succeeded", "failed"])

    async def fake_step(step: RefreshStep) -> dict[str, object]:
        return {"step": step.name, "status": next(outcomes)}

    with patch("scripts.run_local_node.run_step", side_effect=fake_step):
        assert not await run_daily_refresh(state, path, steps=steps, now=now)
    reloaded = RefreshState.load(path)
    assert reloaded.last_success_session is None
    assert reloaded.attempt_session == date(2026, 10, 7)
    assert reloaded.attempts == 1

    async def ok_step(step: RefreshStep) -> dict[str, object]:
        return {"step": step.name, "status": "succeeded"}

    with patch("scripts.run_local_node.run_step", side_effect=ok_step):
        assert await run_daily_refresh(reloaded, path, steps=steps, now=now + timedelta(hours=2))
    assert RefreshState.load(path).last_success_session == date(2026, 10, 7)


def test_corrupt_state_file_starts_fresh(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    assert RefreshState.load(path) == RefreshState()
