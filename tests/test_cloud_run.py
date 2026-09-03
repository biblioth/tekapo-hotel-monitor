import sqlite3
from datetime import UTC, datetime, timedelta

from app.cloud_run import should_skip_scheduled_run
from app.schedule_guard import build_gate_decision, latest_persisted_run


NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


def run_started(minutes_ago: int) -> dict[str, str]:
    return {
        "id": "test-run",
        "started_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
    }


def test_recent_scheduled_run_is_skipped() -> None:
    assert should_skip_scheduled_run(
        run_started(30), event_name="schedule", now=NOW
    )


def test_old_scheduled_run_is_not_skipped() -> None:
    assert not should_skip_scheduled_run(
        run_started(55), event_name="schedule", now=NOW
    )


def test_first_scheduled_run_is_not_skipped() -> None:
    assert not should_skip_scheduled_run(None, event_name="schedule", now=NOW)


def test_manual_run_bypasses_interval_gate() -> None:
    assert not should_skip_scheduled_run(
        run_started(5), event_name="workflow_dispatch", now=NOW
    )


def test_lightweight_gate_reads_latest_persisted_run(tmp_path) -> None:
    database_path = tmp_path / "monitor.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE runs (id TEXT, started_at TEXT)")
        connection.executemany(
            "INSERT INTO runs(id, started_at) VALUES (?, ?)",
            (("older", run_started(90)["started_at"]), ("latest", run_started(10)["started_at"])),
        )

    assert latest_persisted_run(database_path) == {
        "id": "latest",
        "started_at": run_started(10)["started_at"],
    }
    decision = build_gate_decision(
        database_path,
        event_name="schedule",
        minimum_interval_minutes=50,
        now=NOW,
    )
    assert decision["should_run"] is False


def test_lightweight_gate_runs_when_state_is_missing(tmp_path) -> None:
    decision = build_gate_decision(
        tmp_path / "missing.db",
        event_name="schedule",
        minimum_interval_minutes=50,
        now=NOW,
    )

    assert decision["should_run"] is True
