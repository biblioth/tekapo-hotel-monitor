from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from app.config import Settings
from app.database import Database
from app.models import Hotel, HotelResult

logger = logging.getLogger(__name__)


class Provider(Protocol):
    async def check(self, hotel: Hotel) -> HotelResult: ...


class Notifier(Protocol):
    async def send(self, event: dict[str, Any]) -> None: ...


class MonitorService:
    def __init__(
        self,
        settings: Settings,
        hotels: tuple[Hotel, ...],
        provider: Provider,
        notifier: Notifier,
        database: Database,
    ):
        self.settings = settings
        self.hotels = hotels
        self.provider = provider
        self.notifier = notifier
        self.database = database
        self.lock = asyncio.Lock()

    async def run_once(self, trigger: str = "manual") -> dict[str, Any]:
        if self.lock.locked():
            return {"status": "skipped", "reason": "a check is already running"}
        async with self.lock:
            started = datetime.now(UTC)
            start_clock = time.monotonic()
            run_id = str(uuid.uuid4())
            self.database.start_run(run_id, trigger, started.isoformat())
            logger.info("Availability check started", extra={"run_id": run_id})

            results = await asyncio.gather(
                *(self.provider.check(hotel) for hotel in self.hotels),
                return_exceptions=True,
            )
            checked = available = changes = errors = 0
            hotel_summary: list[dict[str, Any]] = []
            for hotel, item in zip(self.hotels, results, strict=True):
                if isinstance(item, BaseException):
                    result = HotelResult(hotel=hotel, status="error", message=str(item))
                else:
                    result = item
                checked += 1
                available += result.status == "available"
                errors += result.status == "error"
                changes += self.database.record_result(run_id, result)
                hotel_summary.append(
                    {
                        "key": hotel.key,
                        "status": result.status,
                        "offers": len(result.offers),
                        "lowest_price": result.lowest_price,
                        "message": result.message,
                    }
                )
                logger.info(
                    "Hotel checked: status=%s offers=%d",
                    result.status,
                    len(result.offers),
                    extra={"run_id": run_id, "hotel_key": hotel.key},
                )

            notifications, notify_errors = await self._flush_outbox()
            duration_ms = int((time.monotonic() - start_clock) * 1000)
            status = "success" if errors == 0 and notify_errors == 0 else "partial"
            self.database.finish_run(
                run_id,
                finished_at=datetime.now(UTC).isoformat(),
                status=status,
                checked_count=checked,
                available_count=available,
                change_count=changes,
                notification_count=notifications,
                error_count=errors + notify_errors,
                duration_ms=duration_ms,
                summary={"hotels": hotel_summary},
            )
            self.database.cleanup(self.settings.log_retention_days)
            summary = {
                "run_id": run_id,
                "status": status,
                "checked": checked,
                "available": available,
                "changes": changes,
                "notifications": notifications,
                "errors": errors + notify_errors,
                "duration_ms": duration_ms,
                "hotels": hotel_summary,
            }
            logger.info(
                "Availability check finished: %s",
                summary,
                extra={"run_id": run_id, "duration_ms": duration_ms},
            )
            return summary

    async def _flush_outbox(self) -> tuple[int, int]:
        sent = errors = 0
        for event in self.database.pending_events():
            try:
                await self.notifier.send(event)
                self.database.mark_event_sent(event["id"])
                sent += 1
                logger.info(
                    "Alert sent",
                    extra={"run_id": event["run_id"], "hotel_key": event["hotel_key"], "event_type": event["event_type"]},
                )
            except Exception as exc:
                self.database.mark_event_failed(event["id"], str(exc))
                errors += 1
                logger.warning(
                    "Alert delivery failed: %s",
                    exc,
                    extra={"run_id": event["run_id"], "hotel_key": event["hotel_key"], "event_type": event["event_type"]},
                )
        return sent, errors

