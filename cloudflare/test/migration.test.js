import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { Miniflare, convertV4MiniflareOptions } from "miniflare";

async function executeSqlFile(db, path) {
  const sql = await readFile(path, "utf8");
  for (const statement of sql.split(";").map((value) => value.trim()).filter(Boolean)) {
    await db.prepare(statement).run();
  }
}

test("v2 migration upgrades the original event table without losing rows", async () => {
  const mf = new Miniflare(
    convertV4MiniflareOptions({
      name: "migration-test",
      modules: true,
      script: "export default { fetch() { return new Response('ok') } }",
      d1Databases: ["DB"],
    }),
  );
  try {
    const db = await mf.getD1Database("DB");
    await db
      .prepare(
        `CREATE TABLE sensor_events (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           idempotency_key TEXT NOT NULL UNIQUE,
           cycle_id TEXT NOT NULL,
           hotel_key TEXT NOT NULL,
           event_type TEXT NOT NULL,
           payload_json TEXT NOT NULL,
           requires_validation INTEGER NOT NULL DEFAULT 1,
           created_at TEXT NOT NULL,
           validation_dispatched_at TEXT,
           notified_at TEXT,
           notify_attempts INTEGER NOT NULL DEFAULT 0,
           last_error TEXT
         )`,
      )
      .run();
    await db
      .prepare(
        `INSERT INTO sensor_events(
           idempotency_key, cycle_id, hotel_key, event_type, payload_json,
           requires_validation, created_at
         ) VALUES ('existing-event', 'cycle-1', 'ranginui', 'availability_returned', '{}', 0, ?)`,
      )
      .bind("2026-09-16T00:00:00.000Z")
      .run();

    await executeSqlFile(db, new URL("../migrate-v2.sql", import.meta.url));

    const event = await db
      .prepare(
        `SELECT idempotency_key, validation_status, validation_attempts
         FROM sensor_events WHERE idempotency_key='existing-event'`,
      )
      .first();
    assert.deepEqual(event, {
      idempotency_key: "existing-event",
      validation_status: "not_required",
      validation_attempts: 0,
    });
    const deliveries = await db
      .prepare(
        `SELECT name FROM sqlite_master
         WHERE type='table' AND name IN ('sensor_deliveries', 'sensor_summary_deliveries')
         ORDER BY name`,
      )
      .all();
    assert.deepEqual(
      deliveries.results.map((row) => row.name),
      ["sensor_deliveries", "sensor_summary_deliveries"],
    );
  } finally {
    await mf.dispose();
  }
});
