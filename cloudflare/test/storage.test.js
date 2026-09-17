import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { Miniflare, convertV4MiniflareOptions } from "miniflare";

import {
  completeValidation,
  ensureDeliveries,
  ensureSummaryDeliveries,
  hotelCheckDecision,
  markDeliveryDelivered,
  markSummaryDeliveryDelivered,
  pendingDeliveries,
  pendingSummaryDeliveries,
  pendingConfirmedEvents,
  reconcileStaleCycles,
  recordObservation,
  startCycle,
} from "../src/storage.js";

const hotel = {
  key: "hotel-a",
  name: "Hotel A",
  checkIn: "2027-02-05",
  checkOut: "2027-02-06",
};
const room = {
  identity: "room-a",
  roomName: "Room A",
  link: "https://example.com",
};

async function database() {
  const mf = new Miniflare(
    convertV4MiniflareOptions({
      name: "storage-test",
      modules: true,
      script: "export default { fetch() { return new Response('ok') } }",
      d1Databases: ["DB"],
    }),
  );
  try {
    const db = await mf.getD1Database("DB");
    const schema = await readFile(new URL("../schema.sql", import.meta.url), "utf8");
    for (const statement of schema.split(";").map((value) => value.trim()).filter(Boolean)) {
      await db.prepare(statement).run();
    }
    return { db, close: () => mf.dispose() };
  } catch (error) {
    await mf.dispose();
    throw error;
  }
}

test("stale running cycles are closed without touching a fresh cycle", async () => {
  const { db, close } = await database();
  try {
    await startCycle(db, "cycle-stale", "2026-09-16T00:00:00Z", "2026-09-16T00:00:01Z");
    await startCycle(db, "cycle-fresh", "2026-09-16T00:20:00Z", "2026-09-16T00:20:01Z");

    const recovered = await reconcileStaleCycles(
      db,
      "2026-09-16T00:15:00Z",
      "2026-09-16T00:25:00Z",
    );
    assert.equal(recovered, 1);

    const rows = await db
      .prepare("SELECT id, status, finished_at FROM sensor_cycles ORDER BY id")
      .all();
    assert.deepEqual(rows.results, [
      { id: "cycle-fresh", status: "running", finished_at: null },
      { id: "cycle-stale", status: "error", finished_at: "2026-09-16T00:25:00Z" },
    ]);
  } finally {
    await close();
  }
});

test("candidate transitions wait for browser confirmation without advancing snapshot", async () => {
  const { db, close } = await database();
  try {
    await startCycle(db, "cycle-1", "2026-09-16T00:00:00Z", "2026-09-16T00:00:00Z");
    await recordObservation(db, {
      cycleId: "cycle-1",
      hotel,
      observation: {
        status: "unavailable",
        confidence: "confirmed",
        offers: [],
        message: null,
      },
      durationMs: 1,
      observedAt: "2026-09-16T00:00:01Z",
    });

    await startCycle(db, "cycle-2", "2026-09-16T00:05:00Z", "2026-09-16T00:05:00Z");
    const event = await recordObservation(db, {
      cycleId: "cycle-2",
      hotel,
      observation: {
        status: "available",
        confidence: "candidate",
        offers: [room],
        message: null,
      },
      durationMs: 1,
      observedAt: "2026-09-16T00:05:01Z",
    });

    assert.equal(event.requiresValidation, true);
    assert.equal(event.validationStatus, "pending");
    const before = await db
      .prepare("SELECT status FROM sensor_snapshots WHERE hotel_key=?")
      .bind(hotel.key)
      .first();
    assert.equal(before.status, "unavailable");
    assert.equal((await pendingConfirmedEvents(db)).length, 0);

    const completed = await completeValidation(db, {
      eventId: event.id,
      idempotencyKey: event.idempotencyKey,
      hotelKey: hotel.key,
      observation: {
        status: "available",
        offers: [{ ...room, identity: "browser-room-a" }],
        message: null,
      },
      completedAt: "2026-09-16T00:06:00Z",
    });
    assert.equal(completed.confirmed, true);
    const after = await db
      .prepare("SELECT status, offers_json FROM sensor_snapshots WHERE hotel_key=?")
      .bind(hotel.key)
      .first();
    assert.equal(after.status, "available");
    assert.equal(JSON.parse(after.offers_json)[0].identity, "room-a");
    assert.equal((await pendingConfirmedEvents(db)).length, 1);
  } finally {
    await close();
  }
});

test("repeated candidate observations reuse one pending validation", async () => {
  const { db, close } = await database();
  try {
    await startCycle(db, "cycle-1", "2026-09-16T00:00:00Z", "2026-09-16T00:00:00Z");
    await recordObservation(db, {
      cycleId: "cycle-1",
      hotel,
      observation: { status: "unavailable", confidence: "confirmed", offers: [] },
      durationMs: 1,
      observedAt: "2026-09-16T00:00:01Z",
    });
    let first;
    for (const [index, minute] of [5, 10].entries()) {
      const cycleId = `cycle-${index + 2}`;
      await startCycle(
        db,
        cycleId,
        `2026-09-16T00:${minute}:00Z`,
        `2026-09-16T00:${minute}:00Z`,
      );
      const event = await recordObservation(db, {
        cycleId,
        hotel,
        observation: { status: "available", confidence: "candidate", offers: [room] },
        durationMs: 1,
        observedAt: `2026-09-16T00:${minute}:01Z`,
      });
      first ||= event;
      assert.equal(event.id, first.id);
    }
    const count = await db.prepare("SELECT COUNT(*) AS count FROM sensor_events").first();
    assert.equal(count.count, 1);
  } finally {
    await close();
  }
});

test("new-room validation preserves API identities and matches browser room names", async () => {
  const { db, close } = await database();
  const roomB = { identity: "api-room-b", roomName: "Deluxe Lake View Room" };
  try {
    await startCycle(db, "cycle-1", "2026-09-16T00:00:00Z", "2026-09-16T00:00:00Z");
    await recordObservation(db, {
      cycleId: "cycle-1",
      hotel,
      observation: { status: "available", confidence: "confirmed", offers: [room] },
      durationMs: 1,
      observedAt: "2026-09-16T00:00:01Z",
    });
    await startCycle(db, "cycle-2", "2026-09-16T00:05:00Z", "2026-09-16T00:05:00Z");
    const event = await recordObservation(db, {
      cycleId: "cycle-2",
      hotel,
      observation: {
        status: "available",
        confidence: "candidate",
        offers: [room, roomB],
      },
      durationMs: 1,
      observedAt: "2026-09-16T00:05:01Z",
    });
    assert.equal(event.type, "new_room");
    const completed = await completeValidation(db, {
      eventId: event.id,
      idempotencyKey: event.idempotencyKey,
      hotelKey: hotel.key,
      observation: {
        status: "available",
        offers: [
          { identity: "browser-a", roomName: "Room A" },
          { identity: "browser-b", roomName: "Deluxe Lake View Hotel Room" },
        ],
      },
      completedAt: "2026-09-16T00:06:00Z",
    });
    assert.equal(completed.confirmed, true);
    assert.equal(completed.payload.offers[0].identity, "browser-b");
    const snapshot = await db
      .prepare("SELECT offers_json FROM sensor_snapshots WHERE hotel_key=?")
      .bind(hotel.key)
      .first();
    assert.deepEqual(
      JSON.parse(snapshot.offers_json).map((offer) => offer.identity),
      ["room-a", "api-room-b"],
    );
  } finally {
    await close();
  }
});

test("a rejected candidate backs off and can be validated again later", async () => {
  const { db, close } = await database();
  try {
    await startCycle(db, "cycle-1", "2026-09-16T00:00:00Z", "2026-09-16T00:00:00Z");
    await recordObservation(db, {
      cycleId: "cycle-1",
      hotel,
      observation: { status: "unavailable", confidence: "confirmed", offers: [] },
      durationMs: 1,
      observedAt: "2026-09-16T00:00:01Z",
    });
    await startCycle(db, "cycle-2", "2026-09-16T00:05:00Z", "2026-09-16T00:05:00Z");
    const first = await recordObservation(db, {
      cycleId: "cycle-2",
      hotel,
      observation: { status: "available", confidence: "candidate", offers: [room] },
      durationMs: 1,
      observedAt: "2026-09-16T00:05:01Z",
    });
    await completeValidation(db, {
      eventId: first.id,
      idempotencyKey: first.idempotencyKey,
      hotelKey: hotel.key,
      observation: { status: "unavailable", offers: [], message: null },
      completedAt: "2026-09-16T00:06:00Z",
    });
    assert.equal(
      (await hotelCheckDecision(db, hotel.key, new Date("2026-09-16T00:30:00Z"))).shouldCheck,
      false,
    );
    assert.equal(
      (await hotelCheckDecision(db, hotel.key, new Date("2026-09-16T01:07:00Z"))).shouldCheck,
      true,
    );

    await startCycle(db, "cycle-3", "2026-09-16T01:07:00Z", "2026-09-16T01:07:00Z");
    const second = await recordObservation(db, {
      cycleId: "cycle-3",
      hotel,
      observation: { status: "available", confidence: "candidate", offers: [room] },
      durationMs: 1,
      observedAt: "2026-09-16T01:07:01Z",
    });
    assert.notEqual(second.id, first.id);
  } finally {
    await close();
  }
});

test("notification channels complete independently", async () => {
  const { db, close } = await database();
  try {
    await startCycle(db, "cycle-1", "2026-09-16T00:00:00Z", "2026-09-16T00:00:00Z");
    await recordObservation(db, {
      cycleId: "cycle-1",
      hotel,
      observation: { status: "unavailable", confidence: "confirmed", offers: [] },
      durationMs: 1,
      observedAt: "2026-09-16T00:00:01Z",
    });
    await startCycle(db, "cycle-2", "2026-09-16T00:05:00Z", "2026-09-16T00:05:00Z");
    const event = await recordObservation(db, {
      cycleId: "cycle-2",
      hotel,
      observation: { status: "available", confidence: "confirmed", offers: [room] },
      durationMs: 1,
      observedAt: "2026-09-16T00:05:01Z",
    });
    await ensureDeliveries(
      db,
      event.id,
      ["feishu", "pushplus"],
      "2026-09-16T00:05:02Z",
    );
    const deliveries = await pendingDeliveries(db, "2026-09-16T00:05:03Z");
    assert.equal(deliveries.length, 2);

    await markDeliveryDelivered(
      db,
      deliveries[0].deliveryId,
      event.id,
      "2026-09-16T00:05:04Z",
    );
    let row = await db.prepare("SELECT notified_at FROM sensor_events WHERE id=?").bind(event.id).first();
    assert.equal(row.notified_at, null);

    await markDeliveryDelivered(
      db,
      deliveries[1].deliveryId,
      event.id,
      "2026-09-16T00:05:05Z",
    );
    row = await db.prepare("SELECT notified_at FROM sensor_events WHERE id=?").bind(event.id).first();
    assert.equal(row.notified_at, "2026-09-16T00:05:05Z");
  } finally {
    await close();
  }
});

test("daily summary deliveries are idempotent per date and channel", async () => {
  const { db, close } = await database();
  try {
    for (let index = 0; index < 2; index += 1) {
      await ensureSummaryDeliveries(
        db,
        "2026-09-16",
        ["feishu", "pushplus"],
        "summary",
        "title",
        "2026-09-17T00:07:00Z",
      );
    }
    const deliveries = await pendingSummaryDeliveries(db, "2026-09-17T00:08:00Z");
    assert.equal(deliveries.length, 2);
    await markSummaryDeliveryDelivered(
      db,
      deliveries[0].deliveryId,
      "2026-09-17T00:08:01Z",
    );
    const remaining = await pendingSummaryDeliveries(db, "2026-09-17T00:08:02Z");
    assert.equal(remaining.length, 1);
  } finally {
    await close();
  }
});
