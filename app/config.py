from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from app.models import Hotel


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    feishu_webhook_url: str | None
    feishu_webhook_secret: str | None
    pushplus_token: str | None
    pushplus_topic: str | None
    check_in: date
    check_out: date
    adults: int
    currency: str
    timezone: str
    cron_minute: int
    run_on_startup: bool
    browser_timeout_seconds: int
    browser_retries: int
    chromium_executable_path: str | None
    database_path: Path
    log_file: Path
    hotels_file: Path
    log_retention_days: int
    admin_token: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL") or None,
            feishu_webhook_secret=os.getenv("FEISHU_WEBHOOK_SECRET") or None,
            pushplus_token=os.getenv("PUSHPLUS_TOKEN") or None,
            pushplus_topic=os.getenv("PUSHPLUS_TOPIC") or None,
            check_in=date.fromisoformat(os.getenv("CHECK_IN", "2027-02-05")),
            check_out=date.fromisoformat(os.getenv("CHECK_OUT", "2027-02-06")),
            adults=int(os.getenv("ADULTS", "2")),
            currency=os.getenv("CURRENCY", "NZD").upper(),
            timezone=os.getenv("CHECK_TIMEZONE", "Pacific/Auckland"),
            cron_minute=int(os.getenv("CHECK_CRON_MINUTE", "7")),
            run_on_startup=_bool("RUN_ON_STARTUP", True),
            browser_timeout_seconds=int(os.getenv("BROWSER_TIMEOUT_SECONDS", "75")),
            browser_retries=int(os.getenv("BROWSER_RETRIES", "2")),
            chromium_executable_path=os.getenv("CHROMIUM_EXECUTABLE_PATH") or None,
            database_path=Path(os.getenv("DATABASE_PATH", "data/monitor.db")),
            log_file=Path(os.getenv("LOG_FILE", "data/monitor.jsonl")),
            hotels_file=Path(os.getenv("HOTELS_FILE", "hotels.json")),
            log_retention_days=int(os.getenv("LOG_RETENTION_DAYS", "365")),
            admin_token=os.getenv("ADMIN_TOKEN") or None,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.check_out <= self.check_in:
            raise ValueError("CHECK_OUT must be later than CHECK_IN")
        if self.adults < 1:
            raise ValueError("ADULTS must be at least 1")
        if not 0 <= self.cron_minute <= 59:
            raise ValueError("CHECK_CRON_MINUTE must be between 0 and 59")
        if self.browser_timeout_seconds < 15:
            raise ValueError("BROWSER_TIMEOUT_SECONDS must be at least 15")
        if self.browser_retries < 1:
            raise ValueError("BROWSER_RETRIES must be at least 1")
        ZoneInfo(self.timezone)

    def load_hotels(self) -> tuple[Hotel, ...]:
        data = json.loads(self.hotels_file.read_text(encoding="utf-8"))
        hotels = tuple(
            Hotel(
                key=item["key"],
                name=item["name"],
                engine=item["engine"],
                booking_url=item["booking_url"],
                room_names=tuple(item.get("room_names", [])),
                aliases=tuple(item.get("aliases", [])),
                check_in=date.fromisoformat(item["check_in"]) if item.get("check_in") else None,
                check_out=date.fromisoformat(item["check_out"]) if item.get("check_out") else None,
                adults=int(item["adults"]) if item.get("adults") is not None else None,
            )
            for item in data
        )
        keys = [hotel.key for hotel in hotels]
        if len(keys) != len(set(keys)):
            raise ValueError("Hotel keys must be unique")
        for hotel in hotels:
            if (hotel.check_in is None) != (hotel.check_out is None):
                raise ValueError(f"Hotel {hotel.key} must define both check_in and check_out")
            if hotel.check_in and hotel.check_out and hotel.check_out <= hotel.check_in:
                raise ValueError(f"Hotel {hotel.key} check_out must be later than check_in")
            if hotel.adults is not None and hotel.adults < 1:
                raise ValueError(f"Hotel {hotel.key} adults must be at least 1")
        return hotels
