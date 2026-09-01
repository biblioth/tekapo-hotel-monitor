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
        "\n"
        "结论：🔔 发现房态变化，提醒已经发送\n"
        "执行情况：自动检查 2 次，计划约 24 次，少 22 次\n"
        "检查质量：1 次完整成功，1 次未完整成功\n"
        "官网读取失败：\n"
        "- Hahei Beach：1 次\n"
        "房态结果：变化 1 次；已发送提醒 1 条\n"
        "最近有效记录：有房 1 家；无房 2 家\n"
        "你需要做什么：请查看此前的放房提醒，并尽快打开官网确认"
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

    assert "官网读取失败：\n- Hahei Beach：6 次" in summary


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

    assert "结论：⚠️ 监控次数不足；已完成的检查未发现新放房" in summary
    assert "检查质量：全部 1 次均完整成功" in summary


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

    assert "自动检查 20 次，计划约 24 次，少 4 次；另有手动检查 3 次" in summary


def test_reports_missing_daily_runs() -> None:
    assert build_summary(date(2026, 8, 16), [], []) == (
        "📊 LakeWatch 日报｜2026-08-16\n"
        "\n"
        "结论：🚨 昨日监控没有运行\n"
        "执行情况：自动检查 0 次，计划约 24 次\n"
        "房态结果：没有足够数据判断是否出现新放房\n"
        "你需要做什么：订房方面暂不操作；需要尽快检查云端定时任务"
    )
