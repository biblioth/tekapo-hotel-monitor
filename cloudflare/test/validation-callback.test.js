import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { Miniflare, convertV4MiniflareOptions } from "miniflare";

import { health, receiveValidation } from "../src/index.js";

async function database() {
  const mf = new Miniflare(
    convertV4MiniflareOptions({
      name: "validation-callback-test",
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

function request(body) {
  return new Request("https://sensor.example/validation", {
    method: "POST",
    headers: {
      authorization: "Bearer validation-secret",
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

function callback(overrides = {}) {
  return {
    hotel_key: "lakeview-tekapo",
    cycle_id: "sensor-1790006400000",
    observation: { status: "unavailable", offers: [] },
    ...overrides,
  };
}

test("browser-only callbacks are idempotent by originating cycle", async () => {
  const { db, close } = await database();
  const env = { DB: db, SHADOW_MODE: "true", VALIDATION_TOKEN: "validation-secret" };
  try {
    const first = await receiveValidation(request(callback()), env);
    assert.equal(first.status, 200);
    assert.equal((await first.json()).ok, true);

    const second = await receiveValidation(request(callback()), env);
    assert.equal(second.status, 200);
    assert.equal((await second.json()).result.duplicate, true);

    const observations = await db
      .prepare("SELECT COUNT(*) AS count FROM sensor_observations")
      .first();
    assert.equal(observations.count, 1);
  } finally {
    await close();
  }
});

test("eventless callbacks cannot mutate direct-API hotel state", async () => {
  const { db, close } = await database();
  const env = { DB: db, SHADOW_MODE: "true", VALIDATION_TOKEN: "validation-secret" };
  try {
    const response = await receiveValidation(
      request(callback({ hotel_key: "ranginui" })),
      env,
    );
    assert.equal(response.status, 400);
    const observations = await db
      .prepare("SELECT COUNT(*) AS count FROM sensor_observations")
      .first();
    assert.equal(observations.count, 0);
  } finally {
    await close();
  }
});

test("validation callback rejects malformed event pairs and statuses", async () => {
  const { db, close } = await database();
  const env = { DB: db, SHADOW_MODE: "true", VALIDATION_TOKEN: "validation-secret" };
  try {
    const partialEvent = await receiveValidation(
      request(callback({ event_id: "12" })),
      env,
    );
    assert.equal(partialEvent.status, 400);

    const invalidStatus = await receiveValidation(
      request(callback({ observation: { status: "maybe", offers: [] } })),
      env,
    );
    assert.equal(invalidStatus.status, 400);
  } finally {
    await close();
  }
});

test("health checks only sensors in shadow mode and the full path in active mode", async () => {
  const { db, close } = await database();
  const now = new Date().toISOString();
  try {
    await db
      .prepare(
        `INSERT INTO sensor_cycles(
           id, scheduled_at, started_at, finished_at, status, checked_count
         ) VALUES ('sensor-health', ?, ?, ?, 'success', 5)`,
      )
      .bind(now, now, now)
      .run();

    const shadowResponse = await health({ DB: db, SHADOW_MODE: "true" });
    assert.equal(shadowResponse.status, 200);
    const shadowBody = await shadowResponse.json();
    assert.equal(shadowBody.mode, "shadow");
    assert.equal(shadowBody.components.sensor.ok, true);

    const activeResponse = await health({ DB: db, SHADOW_MODE: "false" });
    assert.equal(activeResponse.status, 503);
    const activeBody = await activeResponse.json();
    assert.equal(activeBody.components.browserOnly.ok, false);
    assert.equal(activeBody.components.notifications.ok, false);
  } finally {
    await close();
  }
});
