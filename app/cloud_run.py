from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.database import Database
from app.logging_config import configure_logging
from app.notifier import FanoutNotifier
from app.provider import DirectWebsiteProvider
from app.service import MonitorService


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


async def run_cloud_check() -> dict[str, object]:
    """Run a check, deduplicating redundant GitHub schedule events."""
    settings = Settings.from_env()
    configure_logging(settings.log_file, settings.log_retention_days)
    database = Database(settings.database_path)
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    minimum_interval_minutes = int(os.getenv("MIN_CHECK_INTERVAL_MINUTES", "50"))
    latest_runs = database.latest_runs(1)
    latest_run = latest_runs[0] if latest_runs else None
    if should_skip_scheduled_run(
        latest_run,
        event_name=event_name,
        minimum_interval_minutes=minimum_interval_minutes,
    ):
        return {
            "status": "skipped",
            "reason": "a persisted check started within the minimum interval",
            "minimum_interval_minutes": minimum_interval_minutes,
            "latest_run_id": latest_run["id"],
            "latest_started_at": latest_run["started_at"],
        }

    provider = DirectWebsiteProvider(settings)
    notifier = FanoutNotifier(settings)
    service = MonitorService(
        settings,
        settings.load_hotels(),
        provider,
        notifier,
        database,
    )

    try:
        summary = await service.run_once(trigger="github-actions")
        # GitHub caches only regular files. Move committed WAL pages back into the
        # database before the runner archives its state for the following hour.
        with database.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return summary
    finally:
        await provider.close()
        await notifier.close()


def main() -> None:
    try:
        summary = asyncio.run(run_cloud_check())
    except Exception:
        logging.getLogger(__name__).exception("Cloud monitor run failed")
        raise
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
