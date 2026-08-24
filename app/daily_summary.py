from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database import Database
from app.logging_config import configure_logging
from app.notifier import HOTEL_SHORT_NAMES, FanoutNotifier

logger = logging.getLogger(__name__)


def runs_for_local_date(
    runs: list[dict[str, Any]], target_date: date, timezone: ZoneInfo
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for run in runs:
        started_at = datetime.fromisoformat(str(run["started_at"]))
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if started_at.astimezone(timezone).date() == target_date:
            selected.append(run)
    return selected


def build_summary(
    target_date: date,
    runs: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> str:
    title = f"📊 酒店监控日报｜{target_date.isoformat()}"
    if not runs:
        return f"{title}\n昨日无执行记录，请检查 GitHub Actions。"

    successful = sum(run.get("status") == "success" for run in runs)
    errors = sum(int(run.get("error_count") or 0) for run in runs)
    hotel_errors, affected_hotels, other_errors = error_breakdown(runs, snapshots)
    changes = sum(int(run.get("change_count") or 0) for run in runs)
    notifications = sum(int(run.get("notification_count") or 0) for run in runs)
    available = sum(snapshot.get("status") == "available" for snapshot in snapshots)
    unavailable = sum(snapshot.get("status") == "unavailable" for snapshot in snapshots)

    lines = [title]
    if errors:
        lines.append(f"执行 {len(runs)} 次｜完整正常 {successful} 次")
        if hotel_errors:
            scope = "仅涉及" if len(affected_hotels) == 1 else "涉及"
            names = "、".join(affected_hotels[:3])
            if len(affected_hotels) > 3:
                names += f"等 {len(affected_hotels)} 家"
            lines.append(
                f"酒店检查异常 {hotel_errors} 次｜{scope} {len(affected_hotels)} 家：{names}"
            )
        if other_errors:
            lines.append(f"通知或其他异常 {other_errors} 次")
    else:
        lines.append(f"执行 {len(runs)} 次｜全部正常")
    lines.extend(
        [
            f"放房变化 {changes}｜已提醒 {notifications}",
            f"当前：有房 {available} 家｜无房 {unavailable} 家",
        ]
    )
    return "\n".join(lines)


def error_breakdown(
    runs: list[dict[str, Any]], snapshots: list[dict[str, Any]]
) -> tuple[int, list[str], int]:
    """Separate repeated hotel failures from notification or unclassified errors."""
    names_by_key = {
        str(snapshot["hotel_key"]): str(snapshot["hotel_name"])
        for snapshot in snapshots
        if snapshot.get("hotel_key") and snapshot.get("hotel_name")
    }
    hotel_error_count = 0
    affected_keys: set[str] = set()
    total_errors = 0

    for run in runs:
        total_errors += int(run.get("error_count") or 0)
        summary = run.get("summary") or {}
        for hotel in summary.get("hotels") or []:
            if hotel.get("status") != "error":
                continue
            hotel_error_count += 1
            if hotel.get("key"):
                affected_keys.add(str(hotel["key"]))

    affected_hotels = sorted(
        HOTEL_SHORT_NAMES.get(names_by_key.get(key, key), names_by_key.get(key, key))
        for key in affected_keys
    )
    return hotel_error_count, affected_hotels, max(0, total_errors - hotel_error_count)


async def send_daily_summary() -> dict[str, Any]:
    settings = Settings.from_env()
    configure_logging(settings.log_file, settings.log_retention_days)
    database = Database(settings.database_path)
    notifier = FanoutNotifier(settings)
    summary_timezone = ZoneInfo(os.getenv("SUMMARY_TIMEZONE", "Asia/Shanghai"))
    requested_date = os.getenv("SUMMARY_DATE", "").strip()
    target_date = (
        date.fromisoformat(requested_date)
        if requested_date
        else datetime.now(summary_timezone).date() - timedelta(days=1)
    )
    runs = runs_for_local_date(database.latest_runs(500), target_date, summary_timezone)
    snapshots = database.snapshots()
    message = build_summary(target_date, runs, snapshots)
    hotel_errors, affected_hotels, other_errors = error_breakdown(runs, snapshots)

    try:
        await notifier.send_text(message)
    finally:
        await notifier.close()

    result = {
        "date": target_date.isoformat(),
        "runs": len(runs),
        "errors": sum(int(run.get("error_count") or 0) for run in runs),
        "hotel_errors": hotel_errors,
        "affected_hotels": affected_hotels,
        "other_errors": other_errors,
        "changes": sum(int(run.get("change_count") or 0) for run in runs),
        "notifications": sum(int(run.get("notification_count") or 0) for run in runs),
    }
    logger.info("Daily summary sent: %s", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    asyncio.run(send_daily_summary())


if __name__ == "__main__":
    main()
