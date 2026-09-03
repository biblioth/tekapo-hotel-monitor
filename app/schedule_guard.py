from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def should_skip_scheduled_run(
    latest_run: dict[str, Any] | None,
    *,
    event_name: str,
    now: datetime | None = None,
    minimum_interval_minutes: int = 50,
) -> bool:
    """Keep redundant GitHub cron events from checking sites too frequently."""
    if event_name != "schedule" or latest_run is None:
        return False

    started_at = datetime.fromisoformat(str(latest_run["started_at"]).replace("Z", "+00:00"))
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    return current_time - started_at < timedelta(minutes=minimum_interval_minutes)


def latest_persisted_run(database_path: Path) -> dict[str, str] | None:
    """Read only the timestamp needed by the lightweight workflow gate."""
    if not database_path.exists():
        return None
    try:
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "SELECT id, started_at FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).casefold():
            return None
        raise
    if row is None:
        return None
    return {"id": str(row[0]), "started_at": str(row[1])}


def build_gate_decision(
    database_path: Path,
    *,
    event_name: str,
    minimum_interval_minutes: int,
    now: datetime | None = None,
) -> dict[str, object]:
    latest_run = latest_persisted_run(database_path)
    skipped = should_skip_scheduled_run(
        latest_run,
        event_name=event_name,
        now=now,
        minimum_interval_minutes=minimum_interval_minutes,
    )
    decision: dict[str, object] = {
        "should_run": not skipped,
        "event_name": event_name,
        "minimum_interval_minutes": minimum_interval_minutes,
    }
    if latest_run is not None:
        decision["latest_run_id"] = latest_run["id"]
        decision["latest_started_at"] = latest_run["started_at"]
    decision["reason"] = (
        "a persisted check started within the minimum interval"
        if skipped
        else "a full hotel check is due"
    )
    return decision


def main() -> None:
    decision = build_gate_decision(
        Path(os.getenv("DATABASE_PATH", "data/monitor.db")),
        event_name=os.getenv("LAKEWATCH_TRIGGER_KIND")
        or os.getenv("GITHUB_EVENT_NAME", ""),
        minimum_interval_minutes=int(os.getenv("MIN_CHECK_INTERVAL_MINUTES", "50")),
    )
    print(json.dumps(decision, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
