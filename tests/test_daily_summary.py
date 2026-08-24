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
        "📊 酒店监控日报｜2026-08-16\n"
        "执行 2 次｜完整正常 1 次\n"
        "酒店检查异常 1 次｜仅涉及 1 家：Hahei Beach\n"
        "放房变化 1｜已提醒 1\n"
        "当前：有房 1 家｜无房 2 家"
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

    assert "酒店检查异常 6 次｜仅涉及 1 家：Hahei Beach" in summary


def test_clean_day_is_described_as_all_normal() -> None:
    runs = [
        {
            "status": "success",
            "error_count": 0,
            "change_count": 0,
            "notification_count": 0,
        }
    ]

    assert "执行 1 次｜全部正常" in build_summary(date(2026, 8, 23), runs, [])


def test_reports_missing_daily_runs() -> None:
    assert build_summary(date(2026, 8, 16), [], []) == (
        "📊 酒店监控日报｜2026-08-16\n"
        "昨日无执行记录，请检查 GitHub Actions。"
    )
