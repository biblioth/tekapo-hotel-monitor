from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.config import Settings
from app.database import Database
from app.models import Hotel, HotelResult, Offer
from app.service import MonitorService


class SequenceProvider:
    def __init__(self, results: list[HotelResult]):
        self.results = results

    async def check(self, hotel: Hotel) -> HotelResult:
        return self.results.pop(0)


class RecordingNotifier:
    def __init__(self):
        self.events: list[dict] = []

    async def send(self, event: dict) -> None:
        self.events.append(event)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        feishu_webhook_url=None,
        feishu_webhook_secret=None,
        pushplus_token=None,
        pushplus_topic=None,
        check_in=date(2027, 2, 5),
        check_out=date(2027, 2, 6),
        adults=2,
        currency="NZD",
        timezone="Pacific/Auckland",
        cron_minute=0,
        run_on_startup=False,
        browser_timeout_seconds=30,
        browser_retries=1,
        chromium_executable_path=None,
        database_path=tmp_path / "monitor.db",
        log_file=tmp_path / "monitor.jsonl",
        hotels_file=tmp_path / "hotels.json",
        log_retention_days=365,
        admin_token=None,
    )


@pytest.mark.asyncio
async def test_alerts_only_on_actionable_inventory_changes(tmp_path: Path) -> None:
    hotel = Hotel("peppers", "Peppers Bluewater Resort Lake Tekapo", "accor", "https://example.com")
    room_a = Offer("Official", "Deluxe Lake View Room", "https://example.com/a", "NZ$ 400", 400, official=True)
    room_b = Offer("Official", "One Bedroom Suite", "https://example.com/b", "NZ$ 500", 500, official=True)
    sequence = [
        HotelResult(hotel, "available", (room_a,)),  # Baseline: no alert.
        HotelResult(hotel, "available", (room_a,)),  # No change: no alert.
        HotelResult(hotel, "unavailable"),           # Sold out: no alert.
        HotelResult(hotel, "available", (room_a,)),  # Returned: alert.
        HotelResult(hotel, "available", (room_a, room_b)),  # New room: alert.
    ]
    provider = SequenceProvider(sequence)
    notifier = RecordingNotifier()
    service = MonitorService(make_settings(tmp_path), (hotel,), provider, notifier, Database(tmp_path / "monitor.db"))

    summaries = [await service.run_once() for _ in range(5)]

    assert [summary["changes"] for summary in summaries] == [0, 0, 0, 1, 1]
    assert [event["event_type"] for event in notifier.events] == ["availability_returned", "new_room"]
    assert notifier.events[1]["payload"]["offers"][0]["room_name"] == "One Bedroom Suite"


@pytest.mark.asyncio
async def test_errors_do_not_overwrite_last_good_snapshot(tmp_path: Path) -> None:
    hotel = Hotel("ranginui", "Ranginui at Lake Tekapo", "preno", "https://example.com")
    room = Offer("Official", "Whetu Suite", "https://example.com", "NZ$ 600", 600, official=True)
    provider = SequenceProvider(
        [
            HotelResult(hotel, "unavailable"),
            HotelResult(hotel, "error", message="timeout"),
            HotelResult(hotel, "available", (room,)),
        ]
    )
    notifier = RecordingNotifier()
    service = MonitorService(make_settings(tmp_path), (hotel,), provider, notifier, Database(tmp_path / "monitor.db"))

    await service.run_once()
    failed = await service.run_once()
    recovered = await service.run_once()

    assert failed["status"] == "partial"
    assert recovered["changes"] == 1
    assert notifier.events[0]["event_type"] == "availability_returned"
