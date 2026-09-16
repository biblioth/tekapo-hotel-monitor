from datetime import date

from app.daily_summary import build_summary


def test_builds_concise_daily_summary() -> None:
    runs = [
        {
            "status": "success",
            "error_count": 0,
            "change_count": 0,
            "notification_count": 0,
        },
        {
            "status": "partial",
            "error_count": 1,
            "change_count": 1,
            "notification_count": 1,
            "summary": {
                "hotels": [
                    {"key": "tasman-hahei-beach", "status": "error"},
                ]
            },
        },
    ]
    snapshots = [
        {"hotel_key": "ranginui", "hotel_name": "Ranginui at Lake Tekapo", "status": "available"},
        {"hotel_key": "lakeview", "hotel_name": "Lakeview Tekapo", "status": "unavailable"},
        {
            "hotel_key": "tasman-hahei-beach",
            "hotel_name": "Tasman Holiday Parks Hahei Beach",
            "status": "unavailable",
        },
    ]

    assert build_summary(date(2026, 8, 16), runs, snapshots) == (
        "📊 LakeWatch 日报｜2026-08-16\n"
        "🔔 发现 1 次房态变化｜已发送 1 条提醒\n"
        "自动检查 2/24 次，少 22 次\n"
        "官网异常：Hahei Beach：失败 1 次（截至日报仍未恢复）\n"
        "请查看放房提醒并打开官网确认"
    )


def test_repeated_errors_are_reported_as_one_affected_hotel() -> None:
    runs = [
        {
            "status": "partial",
            "error_count": 1,
            "change_count": 0,
            "notification_count": 0,
            "summary": {"hotels": [{"key": "tasman-hahei-beach", "status": "error"}]},
        }
        for _ in range(6)
    ]
    snapshots = [
        {
            "hotel_key": "tasman-hahei-beach",
            "hotel_name": "Tasman Holiday Parks Hahei Beach",
            "status": "unavailable",
        }
    ]

    summary = build_summary(date(2026, 8, 23), runs, snapshots)

    assert "官网异常：Hahei Beach：失败 6 次（截至日报仍未恢复）" in summary


def test_clean_day_is_described_as_all_normal() -> None:
    runs = [
        {
            "status": "success",
            "error_count": 0,
            "change_count": 0,
            "notification_count": 0,
        }
    ]

    summary = build_summary(date(2026, 8, 23), runs, [])

    assert "⚠️ 监控执行不足｜未发现新房" in summary
    assert "自动检查 1/24 次，少 23 次" in summary


def test_separates_scheduled_and_manual_checks() -> None:
    runs = [
        {
            "trigger": "github-schedule",
            "status": "success",
            "error_count": 0,
            "change_count": 0,
            "notification_count": 0,
        }
        for _ in range(20)
    ]
    runs.extend(
        {
            "trigger": "github-manual",
            "status": "success",
            "error_count": 0,
            "change_count": 0,
            "notification_count": 0,
        }
        for _ in range(3)
    )

    summary = build_summary(date(2026, 8, 31), runs, [])

    assert "自动检查 20/24 次，少 4 次｜另有手动 3 次" in summary


def test_reports_missing_daily_runs() -> None:
    assert build_summary(date(2026, 8, 16), [], []) == (
        "📊 LakeWatch 日报｜2026-08-16\n"
        "🚨 昨日监控未运行\n"
        "自动检查 0/24 次｜无法判断房态"
    )


def test_daily_summary_marks_a_hotel_as_recovered() -> None:
    runs = [
        {
            "started_at": "2026-09-16T02:00:00+00:00",
            "status": "success",
            "error_count": 0,
            "change_count": 0,
            "notification_count": 0,
            "summary": {
                "hotels": [{"key": "tasman-hahei-beach", "status": "unavailable"}]
            },
        },
        {
            "started_at": "2026-09-16T01:00:00+00:00",
            "status": "partial",
            "error_count": 1,
            "change_count": 0,
            "notification_count": 0,
            "summary": {"hotels": [{"key": "tasman-hahei-beach", "status": "error"}]},
        },
    ]
    snapshots = [
        {
            "hotel_key": "tasman-hahei-beach",
            "hotel_name": "Tasman Holiday Parks Hahei Beach",
            "status": "unavailable",
        }
    ]

    summary = build_summary(date(2026, 9, 16), runs, snapshots)

    assert "官网异常：Hahei Beach：失败 1 次（已恢复）" in summary
    assert "系统将继续自动重试｜无需手动处理" in summary
