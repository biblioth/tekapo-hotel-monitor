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
EXPECTED_DAILY_CHECKS = 24
MINIMUM_HEALTHY_CHECKS = 20


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


def execution_breakdown(runs: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Count automatic, manual, and legacy runs whose old trigger was ambiguous."""
    manual = sum(run.get("trigger") in {"manual", "github-manual"} for run in runs)
    scheduled = sum(run.get("trigger") in {"schedule", "github-schedule"} for run in runs)
    unclassified = len(runs) - manual - scheduled
    return scheduled + unclassified, manual, unclassified


def build_summary(
    target_date: date,
    runs: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> str:
    title = f"📊 LakeWatch 日报｜{target_date.isoformat()}"
    if not runs:
        return "\n".join(
            [
                title,
                "🚨 昨日监控未运行",
                f"自动检查 0/{EXPECTED_DAILY_CHECKS} 次｜无法判断房态",
            ]
        )

    errors = sum(int(run.get("error_count") or 0) for run in runs)
    _, hotel_error_details, other_errors = detailed_error_breakdown(runs, snapshots)
    changes = sum(int(run.get("change_count") or 0) for run in runs)
    notifications = sum(int(run.get("notification_count") or 0) for run in runs)
    automatic, manual, _ = execution_breakdown(runs)
    missing = max(0, EXPECTED_DAILY_CHECKS - automatic)
    coverage_low = automatic < MINIMUM_HEALTHY_CHECKS

    if changes or notifications:
        conclusion = f"🔔 发现 {changes} 次房态变化｜已发送 {notifications} 条提醒"
    elif coverage_low and errors:
        conclusion = "⚠️ 监控执行不足且有异常｜未发现新房"
    elif coverage_low:
        conclusion = "⚠️ 监控执行不足｜未发现新房"
    elif errors:
        conclusion = "⚠️ 有短暂异常｜未发现新房"
    else:
        conclusion = "✅ 监控正常｜未发现新房"

    execution = f"自动检查 {automatic}/{EXPECTED_DAILY_CHECKS} 次"
    if missing:
        execution += f"，少 {missing} 次"
    elif automatic > EXPECTED_DAILY_CHECKS:
        execution += f"，多 {automatic - EXPECTED_DAILY_CHECKS} 次"
    if manual:
        execution += f"｜另有手动 {manual} 次"

    lines = [title, conclusion, execution]
    if hotel_error_details:
        recovery = hotel_recovery_status(runs, snapshots)
        details = "；".join(
            f"{name}：失败 {count} 次（{'已恢复' if recovery.get(name) else '截至日报仍未恢复'}）"
            for name, count in hotel_error_details
        )
        lines.append(f"官网异常：{details}")
    if other_errors:
        lines.append(f"通知发送或其他问题：{other_errors} 次")
    if notifications:
        lines.append("请查看放房提醒并打开官网确认")
    elif errors:
        lines.append("系统将继续自动重试｜无需手动处理")
    elif coverage_low:
        lines.append("系统将继续自动监控｜无需手动处理")
    return "\n".join(lines)


def hotel_recovery_status(
    runs: list[dict[str, Any]], snapshots: list[dict[str, Any]]
) -> dict[str, bool]:
    """Report whether each failed hotel had a later successful observation."""
    ordered = sorted(
        runs,
        key=lambda run: str(run.get("started_at") or ""),
        reverse=True,
    )
    latest_status: dict[str, str] = {}
    names_by_key = {
        str(snapshot["hotel_key"]): str(snapshot["hotel_name"])
        for snapshot in snapshots
        if snapshot.get("hotel_key") and snapshot.get("hotel_name")
    }
    for run in ordered:
        for hotel in (run.get("summary") or {}).get("hotels") or []:
            key = str(hotel.get("key") or "unknown")
            latest_status.setdefault(key, str(hotel.get("status") or "unknown"))
    return {
        HOTEL_SHORT_NAMES.get(names_by_key.get(key, key), names_by_key.get(key, key)): status != "error"
        for key, status in latest_status.items()
    }


def detailed_error_breakdown(
    runs: list[dict[str, Any]], snapshots: list[dict[str, Any]]
) -> tuple[int, list[tuple[str, int]], int]:
    """Return per-hotel failure counts with user-facing hotel names."""
    names_by_key = {
        str(snapshot["hotel_key"]): str(snapshot["hotel_name"])
        for snapshot in snapshots
        if snapshot.get("hotel_key") and snapshot.get("hotel_name")
    }
    counts_by_key: dict[str, int] = {}
    total_errors = 0

    for run in runs:
        total_errors += int(run.get("error_count") or 0)
        summary = run.get("summary") or {}
        for hotel in summary.get("hotels") or []:
            if hotel.get("status") != "error":
                continue
            key = str(hotel.get("key") or "unknown")
            counts_by_key[key] = counts_by_key.get(key, 0) + 1

    details = sorted(
        (
            HOTEL_SHORT_NAMES.get(names_by_key.get(key, key), names_by_key.get(key, key)),
            count,
        )
        for key, count in counts_by_key.items()
    )
    hotel_error_count = sum(counts_by_key.values())
    return hotel_error_count, details, max(0, total_errors - hotel_error_count)


def error_breakdown(
    runs: list[dict[str, Any]], snapshots: list[dict[str, Any]]
) -> tuple[int, list[str], int]:
    """Separate repeated hotel failures from notification or unclassified errors."""
    hotel_errors, details, other_errors = detailed_error_breakdown(runs, snapshots)
    return hotel_errors, [name for name, _ in details], other_errors


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
    _, hotel_error_details, _ = detailed_error_breakdown(runs, snapshots)
    automatic, manual, unclassified = execution_breakdown(runs)

    try:
        await notifier.send_text(message)
    finally:
        await notifier.close()

    result = {
        "date": target_date.isoformat(),
        "runs": len(runs),
        "automatic_runs": automatic,
        "manual_runs": manual,
        "legacy_unclassified_runs": unclassified,
        "successful_runs": sum(run.get("status") == "success" for run in runs),
        "errors": sum(int(run.get("error_count") or 0) for run in runs),
        "hotel_errors": hotel_errors,
        "affected_hotels": affected_hotels,
        "hotel_error_counts": dict(hotel_error_details),
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
