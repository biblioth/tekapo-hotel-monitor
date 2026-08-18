from __future__ import annotations

import asyncio
import json
import logging

from app.config import Settings
from app.database import Database
from app.logging_config import configure_logging
from app.notifier import FanoutNotifier
from app.provider import DirectWebsiteProvider
from app.service import MonitorService


async def run_cloud_check() -> dict[str, object]:
    """Run exactly one check for an external scheduler such as GitHub Actions."""
    settings = Settings.from_env()
    configure_logging(settings.log_file, settings.log_retention_days)
    database = Database(settings.database_path)
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
