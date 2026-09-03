from __future__ import annotations

import asyncio
import json
import logging
import os

from app.config import Settings
from app.database import Database
from app.logging_config import configure_logging
from app.notifier import FanoutNotifier
from app.provider import DirectWebsiteProvider
from app.schedule_guard import should_skip_scheduled_run
from app.service import MonitorService


async def run_cloud_check() -> dict[str, object]:
    """Run a check, deduplicating redundant GitHub schedule events."""
    settings = Settings.from_env()
    configure_logging(settings.log_file, settings.log_retention_days)
    database = Database(settings.database_path)
    event_name = os.getenv("LAKEWATCH_TRIGGER_KIND") or os.getenv("GITHUB_EVENT_NAME", "")
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
        trigger = {
            "schedule": "github-schedule",
            "workflow_dispatch": "github-manual",
        }.get(event_name, "github-actions")
        summary = await service.run_once(trigger=trigger)
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
