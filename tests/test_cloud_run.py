from datetime import UTC, datetime, timedelta

from app.cloud_run import should_skip_scheduled_run


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
