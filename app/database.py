from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app.models import HotelResult


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    trigger TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    checked_count INTEGER NOT NULL DEFAULT 0,
                    available_count INTEGER NOT NULL DEFAULT 0,
                    change_count INTEGER NOT NULL DEFAULT 0,
                    notification_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER,
                    summary_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    hotel_key TEXT NOT NULL,
                    hotel_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    offer_count INTEGER NOT NULL,
                    lowest_price REAL,
                    message TEXT,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_observations_run ON observations(run_id);
                CREATE TABLE IF NOT EXISTS snapshots (
                    hotel_key TEXT PRIMARY KEY,
                    hotel_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    offers_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    run_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    hotel_key TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    notified_at TEXT,
                    notify_attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_pending ON events(notified_at, id);
                """
            )

    def start_run(self, run_id: str, trigger: str, started_at: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO runs(id, trigger, started_at, status) VALUES (?, ?, ?, 'running')",
                (run_id, trigger, started_at),
            )

    def finish_run(self, run_id: str, **fields: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs SET finished_at=?, status=?, checked_count=?, available_count=?,
                    change_count=?, notification_count=?, error_count=?, duration_ms=?, summary_json=?
                WHERE id=?
                """,
                (
                    fields["finished_at"],
                    fields["status"],
                    fields["checked_count"],
                    fields["available_count"],
                    fields["change_count"],
                    fields["notification_count"],
                    fields["error_count"],
                    fields["duration_ms"],
                    json.dumps(fields.get("summary", {}), ensure_ascii=False),
                    run_id,
                ),
            )

    def record_result(self, run_id: str, result: HotelResult) -> int:
        now = utcnow()
        offers = [offer.to_dict() | {"identity": offer.identity} for offer in result.offers]
        payload = {
            "property_name": result.property_name,
            "offers": offers,
            "raw_summary": result.raw_summary,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO observations(
                    run_id, hotel_key, hotel_name, status, offer_count, lowest_price,
                    message, payload_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.hotel.key,
                    result.hotel.name,
                    result.status,
                    len(offers),
                    result.lowest_price,
                    result.message,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            if result.status == "error":
                return 0

            previous = conn.execute(
                "SELECT status, offers_json FROM snapshots WHERE hotel_key=?",
                (result.hotel.key,),
            ).fetchone()
            event_type: str | None = None
            event_offers: list[dict[str, Any]] = []
            if previous is not None and result.status == "available":
                if previous["status"] == "unavailable":
                    event_type = "availability_returned"
                    event_offers = offers
                elif previous["status"] == "available":
                    old_ids = {
                        offer["identity"]
                        for offer in json.loads(previous["offers_json"])
                    }
                    event_offers = [offer for offer in offers if offer["identity"] not in old_ids]
                    if event_offers:
                        event_type = "new_room"

            conn.execute(
                """
                INSERT INTO snapshots(hotel_key, hotel_name, status, offers_json, observed_at, run_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(hotel_key) DO UPDATE SET
                    hotel_name=excluded.hotel_name, status=excluded.status,
                    offers_json=excluded.offers_json, observed_at=excluded.observed_at,
                    run_id=excluded.run_id
                """,
                (
                    result.hotel.key,
                    result.hotel.name,
                    result.status,
                    json.dumps(offers, ensure_ascii=False),
                    now,
                    run_id,
                ),
            )
            if event_type:
                conn.execute(
                    """
                    INSERT INTO events(run_id, hotel_key, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        result.hotel.key,
                        event_type,
                        json.dumps(
                            {
                                "hotel_name": result.hotel.name,
                                "property_name": result.property_name,
                                "check_in": (
                                    result.hotel.check_in.isoformat()
                                    if result.hotel.check_in
                                    else None
                                ),
                                "check_out": (
                                    result.hotel.check_out.isoformat()
                                    if result.hotel.check_out
                                    else None
                                ),
                                "offers": event_offers,
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
                return 1
        return 0

    def pending_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE notified_at IS NULL ORDER BY id LIMIT ?", (limit,)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def mark_event_sent(self, event_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE events SET notified_at=?, notify_attempts=notify_attempts+1, last_error=NULL WHERE id=?",
                (utcnow(), event_id),
            )

    def mark_event_failed(self, event_id: int, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE events SET notify_attempts=notify_attempts+1, last_error=? WHERE id=?",
                (error[:1000], event_id),
            )

    def latest_runs(self, limit: int = 24) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) | {"summary": json.loads(row["summary_json"])} for row in rows]

    def snapshots(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM snapshots ORDER BY hotel_name").fetchall()
        return [dict(row) | {"offers": json.loads(row["offers_json"])} for row in rows]

    def cleanup(self, retention_days: int) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
        with self.connect() as conn:
            conn.execute("DELETE FROM observations WHERE observed_at < ?", (cutoff,))
            conn.execute("DELETE FROM events WHERE created_at < ? AND notified_at IS NOT NULL", (cutoff,))
            conn.execute(
                """
                DELETE FROM runs
                WHERE started_at < ?
                  AND NOT EXISTS (SELECT 1 FROM events WHERE events.run_id = runs.id)
                """,
                (cutoff,),
            )
