import { applyObservation } from "./state-machine.js";
import { backoffDecision } from "./backoff.js";

function parseJson(value, fallback) {
  try {
    return JSON.parse(value || "");
  } catch {
    return fallback;
  }
}

function snapshotFromRow(row) {
  return row
    ? {
        status: row.status,
        offers: parseJson(row.offers_json, []),
        consecutiveUnknown: row.consecutive_unknown,
        observedAt: row.observed_at,
        cycleId: row.cycle_id,
      }
    : null;
}

function snapshotStatement(db, hotelKey, snapshot, observedAt, cycleId) {
  return db
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
      hotelKey,
      snapshot.status,
      JSON.stringify(snapshot.offers || []),
      observedAt,
      cycleId,
      snapshot.consecutiveUnknown || 0,
    );
}

function eventFromRow(row) {
  if (!row) return null;
  return {
    id: row.id,
    idempotencyKey: row.idempotency_key,
    hotelKey: row.hotel_key,
    type: row.event_type,
    payload: parseJson(row.payload_json, {}),
    requiresValidation: Boolean(row.requires_validation),
    validationStatus: row.validation_status,
    validationDispatchedAt: row.validation_dispatched_at,
  };
}

function transitionKey(hotelKey, previous, transition) {
  const identities = transition.offers
    .map((offer) => offer.identity || offer.roomName)
    .map((value) => String(value).trim().toLocaleLowerCase("en"))
    .sort()
    .join(",");
  return [hotelKey, previous?.cycleId || "baseline", transition.type, identities].join(":");
}

function roomTokens(value) {
  const ignored = new Set(["hotel", "the"]);
  return new Set(
    String(value || "")
      .normalize("NFKC")
      .toLocaleLowerCase("en")
      .split(/[^a-z0-9]+/)
      .filter((token) => token && !ignored.has(token)),
  );
}

function roomNamesMatch(left, right) {
  const a = roomTokens(left);
  const b = roomTokens(right);
  if (!a.size || !b.size) return false;
  let overlap = 0;
  for (const token of a) overlap += b.has(token) ? 1 : 0;
  return overlap / Math.min(a.size, b.size) >= 0.75;
}

export async function startCycle(db, cycleId, scheduledAt, startedAt) {
  const result = await db
    .prepare(
      `INSERT OR IGNORE INTO sensor_cycles(id, scheduled_at, started_at, status)
       VALUES (?, ?, ?, 'running')`,
    )
    .bind(cycleId, scheduledAt, startedAt)
    .run();
  return Boolean(result.meta?.changes);
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

  const previousRow = await db
    .prepare("SELECT * FROM sensor_snapshots WHERE hotel_key = ?")
    .bind(hotel.key)
    .first();
  const previous = snapshotFromRow(previousRow);
  const transition = applyObservation(previous, observation);

  // An actionable candidate must not replace the last confirmed snapshot.
  // Stable candidates and first-run baselines remain quiet, matching the
  // original monitor's baseline behaviour.
  if (!transition.event) {
    await snapshotStatement(db, hotel.key, transition.snapshot, observedAt, cycleId).run();
    return null;
  }

  const idempotencyKey = transitionKey(hotel.key, previous, transition.event);
  const requiresValidation = observation.confidence !== "confirmed";
  const payload = {
    hotelName: hotel.name,
    checkIn: hotel.checkIn,
    checkOut: hotel.checkOut,
    confidence: observation.confidence,
    offers: transition.event.offers,
    snapshotStatus: observation.status,
    snapshotOffers: observation.offers || [],
    previousSnapshot: previous
      ? { status: previous.status, offers: previous.offers, consecutiveUnknown: 0 }
      : null,
  };
  const insertEvent = db
    .prepare(
      `INSERT OR IGNORE INTO sensor_events(
         idempotency_key, cycle_id, hotel_key, event_type, payload_json,
         requires_validation, validation_status, created_at
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .bind(
      idempotencyKey,
      cycleId,
      hotel.key,
      transition.event.type,
      JSON.stringify(payload),
      requiresValidation ? 1 : 0,
      requiresValidation ? "pending" : "not_required",
      observedAt,
    );

  let inserted;
  if (requiresValidation) {
    inserted = await insertEvent.run();
  } else {
    const results = await db.batch([
      snapshotStatement(db, hotel.key, transition.snapshot, observedAt, cycleId),
      insertEvent,
    ]);
    inserted = results[1];
  }

  const row = await db
    .prepare("SELECT * FROM sensor_events WHERE idempotency_key = ?")
    .bind(idempotencyKey)
    .first();
  const event = eventFromRow(row);
  if (!event) return null;
  event.created = Boolean(inserted?.meta?.changes);
  return event;
}

export async function markValidationDispatched(db, eventId, dispatchedAt) {
  await db
    .prepare(
      `UPDATE sensor_events
       SET validation_status='dispatched', validation_dispatched_at=?,
           validation_attempts=validation_attempts+1, last_error=NULL
       WHERE id=? AND requires_validation=1
         AND validation_status IN ('pending', 'dispatched')`,
    )
    .bind(dispatchedAt, eventId)
    .run();
}

export async function markValidationDispatchFailed(db, eventId, error) {
  await db
    .prepare(
      `UPDATE sensor_events
       SET validation_status='pending', validation_attempts=validation_attempts+1,
           last_error=?
       WHERE id=? AND requires_validation=1`,
    )
    .bind(String(error).slice(0, 1000), eventId)
    .run();
}

export async function pendingValidations(db, staleBefore, limit = 10) {
  const result = await db
    .prepare(
      `SELECT * FROM sensor_events
       WHERE requires_validation=1
         AND validation_status IN ('pending', 'dispatched')
         AND (validation_dispatched_at IS NULL OR validation_dispatched_at < ?)
       ORDER BY id LIMIT ?`,
    )
    .bind(staleBefore, limit)
    .all();
  return (result.results || []).map(eventFromRow);
}

export async function completeValidation(
  db,
  { eventId, idempotencyKey, hotelKey, observation, completedAt },
) {
  const row = await db
    .prepare(
      `SELECT * FROM sensor_events
       WHERE id=? AND idempotency_key=? AND hotel_key=?`,
    )
    .bind(eventId, idempotencyKey, hotelKey)
    .first();
  const event = eventFromRow(row);
  if (!event) throw new Error("Validation event was not found or did not match");
  if (["confirmed", "rejected"].includes(event.validationStatus)) return event;

  if (observation.status === "unknown") {
    await db
      .prepare(
        `UPDATE sensor_events SET validation_status='pending', last_error=?,
         validation_result_json=? WHERE id=?`,
      )
      .bind(
        String(observation.message || "Browser validation was inconclusive").slice(0, 1000),
        JSON.stringify(observation),
        event.id,
      )
      .run();
    return { ...event, validationStatus: "pending", confirmed: false };
  }

  if (observation.status !== "available") {
    const statements = [
      db
        .prepare(
          `UPDATE sensor_events SET validation_status='rejected',
           validation_completed_at=?, validation_result_json=?, last_error=NULL
           WHERE id=?`,
        )
        .bind(completedAt, JSON.stringify(observation), event.id),
    ];
    if (event.payload.previousSnapshot) {
      statements.unshift(
        snapshotStatement(
          db,
          hotelKey,
          { ...event.payload.previousSnapshot, consecutiveUnknown: 2 },
          completedAt,
          `rejection-${event.id}`,
        ),
      );
    }
    await db.batch(statements);
    return { ...event, validationStatus: "rejected", confirmed: false };
  }

  const browserOffers = observation.offers || [];
  const previous = event.payload.previousSnapshot;
  const matchedBrowserOffers =
    event.type === "new_room"
      ? browserOffers.filter((browserOffer) =>
          (event.payload.offers || []).some((candidateOffer) =>
            roomNamesMatch(candidateOffer.roomName, browserOffer.roomName),
          ),
        )
      : browserOffers;
  if (event.type === "new_room" && matchedBrowserOffers.length === 0) {
    const statements = [
      db
        .prepare(
          `UPDATE sensor_events SET validation_status='rejected',
           validation_completed_at=?, validation_result_json=?,
           last_error='Browser did not confirm a newly appearing room'
           WHERE id=?`,
        )
        .bind(completedAt, JSON.stringify(observation), event.id),
    ];
    if (previous) {
      statements.unshift(
        snapshotStatement(
          db,
          hotelKey,
          { ...previous, consecutiveUnknown: 2 },
          completedAt,
          `rejection-${event.id}`,
        ),
      );
    }
    await db.batch(statements);
    return { ...event, validationStatus: "rejected", confirmed: false };
  }

  // Keep the API adapter's identities in the authoritative snapshot. Browser
  // offer identities are usually derived from room names and would otherwise
  // make every following API observation look like a new room.
  const snapshotOffers = event.payload.snapshotOffers?.length
    ? event.payload.snapshotOffers
    : browserOffers;
  const notificationOffers = matchedBrowserOffers.length
    ? matchedBrowserOffers
    : event.payload.offers;
  const payload = {
    ...event.payload,
    confidence: "browser-confirmed",
    offers: notificationOffers,
    snapshotStatus: "available",
    snapshotOffers,
  };
  await db.batch([
    snapshotStatement(
      db,
      hotelKey,
      { status: "available", offers: snapshotOffers, consecutiveUnknown: 0 },
      completedAt,
      `validation-${event.id}`,
    ),
    db
      .prepare(
        `UPDATE sensor_events SET requires_validation=0,
         validation_status='confirmed', validation_completed_at=?,
         validation_result_json=?, payload_json=?, last_error=NULL
         WHERE id=?`,
      )
      .bind(completedAt, JSON.stringify(observation), JSON.stringify(payload), event.id),
  ]);
  return { ...event, payload, requiresValidation: false, validationStatus: "confirmed", confirmed: true };
}

export async function pendingConfirmedEvents(db, limit = 20) {
  const result = await db
    .prepare(
      `SELECT e.* FROM sensor_events e
       WHERE e.notified_at IS NULL AND e.requires_validation=0
         AND e.validation_status IN ('not_required', 'confirmed')
         AND NOT EXISTS (
           SELECT 1 FROM sensor_deliveries d WHERE d.event_id=e.id
         )
       ORDER BY e.id LIMIT ?`,
    )
    .bind(limit)
    .all();
  return (result.results || []).map(eventFromRow);
}

export async function ensureDeliveries(db, eventId, channels, createdAt) {
  if (!channels.length) return;
  await db.batch(
    channels.map((channel) =>
      db
        .prepare(
          `INSERT OR IGNORE INTO sensor_deliveries(
             event_id, channel, status, created_at, updated_at
           ) VALUES (?, ?, 'pending', ?, ?)`,
        )
        .bind(eventId, channel, createdAt, createdAt),
    ),
  );
}

export async function pendingDeliveries(db, staleBefore, limit = 40) {
  const result = await db
    .prepare(
      `SELECT d.id, d.event_id, d.channel, d.attempts, e.idempotency_key,
              e.hotel_key, e.event_type, e.payload_json
       FROM sensor_deliveries d
       JOIN sensor_events e ON e.id=d.event_id
       WHERE d.delivered_at IS NULL
         AND (d.queue_enqueued_at IS NULL OR d.queue_enqueued_at < ?)
       ORDER BY d.id LIMIT ?`,
    )
    .bind(staleBefore, limit)
    .all();
  return (result.results || []).map((row) => ({
    deliveryId: row.id,
    eventId: row.event_id,
    channel: row.channel,
    attempts: row.attempts,
    event: {
      id: row.event_id,
      idempotencyKey: row.idempotency_key,
      hotelKey: row.hotel_key,
      type: row.event_type,
      payload: parseJson(row.payload_json, {}),
    },
  }));
}

export async function markDeliveryQueued(db, deliveryId, queuedAt) {
  await db
    .prepare(
      `UPDATE sensor_deliveries SET queue_enqueued_at=?, updated_at=?
       WHERE id=? AND delivered_at IS NULL`,
    )
    .bind(queuedAt, queuedAt, deliveryId)
    .run();
}

export async function getDelivery(db, deliveryId) {
  const row = await db
    .prepare(
      `SELECT d.id, d.event_id, d.channel, d.attempts, d.delivered_at,
              e.idempotency_key, e.hotel_key, e.event_type, e.payload_json
       FROM sensor_deliveries d
       JOIN sensor_events e ON e.id=d.event_id WHERE d.id=?`,
    )
    .bind(deliveryId)
    .first();
  if (!row) return null;
  return {
    deliveryId: row.id,
    eventId: row.event_id,
    channel: row.channel,
    attempts: row.attempts,
    deliveredAt: row.delivered_at,
    event: {
      id: row.event_id,
      idempotencyKey: row.idempotency_key,
      hotelKey: row.hotel_key,
      type: row.event_type,
      payload: parseJson(row.payload_json, {}),
    },
  };
}

export async function markDeliveryDelivered(db, deliveryId, eventId, deliveredAt) {
  await db.batch([
    db
      .prepare(
        `UPDATE sensor_deliveries SET status='delivered', attempts=attempts+1,
         delivered_at=?, last_error=NULL, updated_at=? WHERE id=?`,
      )
      .bind(deliveredAt, deliveredAt, deliveryId),
    db
      .prepare(
        `UPDATE sensor_events SET notified_at=?, notify_attempts=notify_attempts+1
         WHERE id=? AND NOT EXISTS (
           SELECT 1 FROM sensor_deliveries
           WHERE event_id=? AND id<>? AND delivered_at IS NULL
         )`,
      )
      .bind(deliveredAt, eventId, eventId, deliveryId),
  ]);
}

export async function markDeliveryFailed(db, deliveryId, error, updatedAt) {
  await db
    .prepare(
      `UPDATE sensor_deliveries SET status='retrying', attempts=attempts+1,
       last_error=?, updated_at=? WHERE id=? AND delivered_at IS NULL`,
    )
    .bind(String(error).slice(0, 1000), updatedAt, deliveryId)
    .run();
}

export async function ensureSummaryDeliveries(
  db,
  summaryDate,
  channels,
  message,
  title,
  createdAt,
) {
  if (!channels.length) return;
  await db.batch(
    channels.map((channel) =>
      db
        .prepare(
          `INSERT OR IGNORE INTO sensor_summary_deliveries(
             summary_date, channel, message, title, status, created_at, updated_at
           ) VALUES (?, ?, ?, ?, 'pending', ?, ?)`,
        )
        .bind(summaryDate, channel, message, title, createdAt, createdAt),
    ),
  );
}

export async function pendingSummaryDeliveries(db, staleBefore, limit = 10) {
  const result = await db
    .prepare(
      `SELECT id, summary_date, channel, message, title, attempts
       FROM sensor_summary_deliveries
       WHERE delivered_at IS NULL
         AND (queue_enqueued_at IS NULL OR queue_enqueued_at < ?)
       ORDER BY id LIMIT ?`,
    )
    .bind(staleBefore, limit)
    .all();
  return (result.results || []).map((row) => ({
    deliveryId: row.id,
    summaryDate: row.summary_date,
    channel: row.channel,
    message: row.message,
    title: row.title,
    attempts: row.attempts,
  }));
}

export async function getSummaryDelivery(db, deliveryId) {
  const row = await db
    .prepare(
      `SELECT id, summary_date, channel, message, title, attempts, delivered_at
       FROM sensor_summary_deliveries WHERE id=?`,
    )
    .bind(deliveryId)
    .first();
  if (!row) return null;
  return {
    deliveryId: row.id,
    summaryDate: row.summary_date,
    channel: row.channel,
    message: row.message,
    title: row.title,
    attempts: row.attempts,
    deliveredAt: row.delivered_at,
  };
}

export async function markSummaryDeliveryQueued(db, deliveryId, queuedAt) {
  await db
    .prepare(
      `UPDATE sensor_summary_deliveries SET queue_enqueued_at=?, updated_at=?
       WHERE id=? AND delivered_at IS NULL`,
    )
    .bind(queuedAt, queuedAt, deliveryId)
    .run();
}

export async function markSummaryDeliveryDelivered(db, deliveryId, deliveredAt) {
  await db
    .prepare(
      `UPDATE sensor_summary_deliveries SET status='delivered', attempts=attempts+1,
       delivered_at=?, last_error=NULL, updated_at=? WHERE id=?`,
    )
    .bind(deliveredAt, deliveredAt, deliveryId)
    .run();
}

export async function markSummaryDeliveryFailed(db, deliveryId, error, updatedAt) {
  await db
    .prepare(
      `UPDATE sensor_summary_deliveries SET status='retrying', attempts=attempts+1,
       last_error=?, updated_at=? WHERE id=? AND delivered_at IS NULL`,
    )
    .bind(String(error).slice(0, 1000), updatedAt, deliveryId)
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

export async function cleanupSensorData(db, cutoff) {
  await db.batch([
    db.prepare("DELETE FROM sensor_observations WHERE observed_at < ?").bind(cutoff),
    db
      .prepare(
        `DELETE FROM sensor_deliveries WHERE delivered_at IS NOT NULL AND delivered_at < ?`,
      )
      .bind(cutoff),
    db
      .prepare(
        `DELETE FROM sensor_events WHERE created_at < ?
         AND validation_status IN ('rejected', 'confirmed', 'not_required')
         AND NOT EXISTS (
           SELECT 1 FROM sensor_deliveries
           WHERE sensor_deliveries.event_id=sensor_events.id
         )`,
      )
      .bind(cutoff),
    db.prepare("DELETE FROM sensor_cycles WHERE scheduled_at < ?").bind(cutoff),
    db
      .prepare(
        `DELETE FROM sensor_summary_deliveries
         WHERE delivered_at IS NOT NULL AND delivered_at < ?`,
      )
      .bind(cutoff),
  ]);
}
