import { applyObservation } from "./state-machine.js";
import { backoffDecision } from "./backoff.js";

function parseOffers(value) {
  try {
    return JSON.parse(value || "[]");
  } catch {
    return [];
  }
}

export async function startCycle(db, cycleId, scheduledAt, startedAt) {
  await db
    .prepare(
      `INSERT OR IGNORE INTO sensor_cycles(id, scheduled_at, started_at, status)
       VALUES (?, ?, ?, 'running')`,
    )
    .bind(cycleId, scheduledAt, startedAt)
    .run();
}

export async function hotelCheckDecision(db, hotelKey, now) {
  const snapshot = await db
    .prepare(
      `SELECT observed_at, consecutive_unknown
       FROM sensor_snapshots WHERE hotel_key = ?`,
    )
    .bind(hotelKey)
    .first();
  return backoffDecision(snapshot, now);
}

export async function recordObservation(
  db,
  { cycleId, hotel, observation, durationMs, observedAt },
) {
  await db
    .prepare(
      `INSERT INTO sensor_observations(
         cycle_id, hotel_key, status, confidence, offers_json, message, duration_ms, observed_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      cycleId,
      hotel.key,
      observation.status,
      observation.confidence,
      JSON.stringify(observation.offers || []),
      observation.message || null,
      durationMs,
      observedAt,
    )
    .run();

  const row = await db
    .prepare("SELECT * FROM sensor_snapshots WHERE hotel_key = ?")
    .bind(hotel.key)
    .first();
  const previous = row
    ? {
        status: row.status,
        offers: parseOffers(row.offers_json),
        consecutiveUnknown: row.consecutive_unknown,
      }
    : null;
  const transition = applyObservation(previous, observation);

  await db
    .prepare(
      `INSERT INTO sensor_snapshots(
         hotel_key, status, offers_json, observed_at, cycle_id, consecutive_unknown
       ) VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(hotel_key) DO UPDATE SET
         status=excluded.status,
         offers_json=excluded.offers_json,
         observed_at=excluded.observed_at,
         cycle_id=excluded.cycle_id,
         consecutive_unknown=excluded.consecutive_unknown`,
    )
    .bind(
      hotel.key,
      transition.snapshot.status,
      JSON.stringify(transition.snapshot.offers),
      observedAt,
      cycleId,
      transition.snapshot.consecutiveUnknown,
    )
    .run();

  if (!transition.event) return null;
  const identities = transition.event.offers
    .map((offer) => offer.identity || offer.roomName)
    .sort()
    .join(",");
  const idempotencyKey = `${cycleId}:${hotel.key}:${transition.event.type}:${identities}`;
  const payload = {
    hotelName: hotel.name,
    checkIn: hotel.checkIn,
    checkOut: hotel.checkOut,
    confidence: observation.confidence,
    offers: transition.event.offers,
  };
  const result = await db
    .prepare(
      `INSERT OR IGNORE INTO sensor_events(
         idempotency_key, cycle_id, hotel_key, event_type, payload_json,
         requires_validation, created_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      idempotencyKey,
      cycleId,
      hotel.key,
      transition.event.type,
      JSON.stringify(payload),
      observation.confidence === "confirmed" ? 0 : 1,
      observedAt,
    )
    .run();
  if (!result.meta?.changes) return null;
  return {
    id: result.meta?.last_row_id,
    idempotencyKey,
    hotelKey: hotel.key,
    type: transition.event.type,
    payload,
    requiresValidation: observation.confidence !== "confirmed",
  };
}

export async function pendingConfirmedEvents(db, limit = 20) {
  const result = await db
    .prepare(
      `SELECT id, idempotency_key, hotel_key, event_type, payload_json
       FROM sensor_events
       WHERE notified_at IS NULL AND requires_validation = 0
       ORDER BY id LIMIT ?`,
    )
    .bind(limit)
    .all();
  return (result.results || []).map((row) => ({
    id: row.id,
    idempotencyKey: row.idempotency_key,
    hotelKey: row.hotel_key,
    type: row.event_type,
    payload: JSON.parse(row.payload_json),
  }));
}

export async function markEventNotified(db, eventId, notifiedAt) {
  await db
    .prepare(
      `UPDATE sensor_events
       SET notified_at=?, notify_attempts=notify_attempts+1, last_error=NULL
       WHERE id=?`,
    )
    .bind(notifiedAt, eventId)
    .run();
}

export async function markEventFailed(db, eventId, error) {
  await db
    .prepare(
      `UPDATE sensor_events
       SET notify_attempts=notify_attempts+1, last_error=?
       WHERE id=?`,
    )
    .bind(String(error).slice(0, 1000), eventId)
    .run();
}

export async function finishCycle(db, cycleId, fields) {
  await db
    .prepare(
      `UPDATE sensor_cycles SET
         finished_at=?, status=?, checked_count=?, available_count=?, unknown_count=?,
         skipped_count=?, event_count=?, duration_ms=?
       WHERE id=?`,
    )
    .bind(
      fields.finishedAt,
      fields.status,
      fields.checkedCount,
      fields.availableCount,
      fields.unknownCount,
      fields.skippedCount,
      fields.eventCount,
      fields.durationMs,
      cycleId,
    )
    .run();
}
