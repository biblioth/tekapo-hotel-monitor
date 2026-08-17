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
        },
    ]
    snapshots = [
        {"status": "available"},
        {"status": "unavailable"},
        {"status": "unavailable"},
    ]

    assert build_summary(date(2026, 8, 16), runs, snapshots) == (
        "📊 酒店监控日报｜2026-08-16\n"
        "执行 2 次｜成功 1｜异常 1\n"
        "放房变化 1｜已提醒 1\n"
        "当前：有房 1 家｜无房 2 家"
    )


def test_reports_missing_daily_runs() -> None:
    assert build_summary(date(2026, 8, 16), [], []) == (
        "📊 酒店监控日报｜2026-08-16\n"
        "昨日无执行记录，请检查 GitHub Actions。"
    )
