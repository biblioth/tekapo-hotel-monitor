import { checkAccor } from "./adapters/accor.js";
import { checkAgilysys } from "./adapters/agilysys.js";
import { checkIbex } from "./adapters/ibex.js";
import { checkNewbook } from "./adapters/newbook.js";
import { checkStaah } from "./adapters/staah.js";
import { dispatchBrowserValidation } from "./github.js";
import { HOTELS } from "./hotels.js";
import { unknown } from "./http.js";
import { sendEventNotifications } from "./notifications.js";
import {
  finishCycle,
  hotelCheckDecision,
  markEventFailed,
  markEventNotified,
  pendingConfirmedEvents,
  recordObservation,
  startCycle,
} from "./storage.js";

const ADAPTERS = {
  accor: checkAccor,
  agilysys: checkAgilysys,
  ibex: checkIbex,
  newbook: checkNewbook,
  staah: checkStaah,
};

async function mapWithConcurrency(items, limit, operation) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const index = next++;
      results[index] = await operation(items[index]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

async function flushNotificationOutbox(env, fetcher) {
  if (env.SHADOW_MODE === "true") return 0;
  let delivered = 0;
  for (const event of await pendingConfirmedEvents(env.DB)) {
    try {
      const result = await sendEventNotifications(env, event, fetcher);
      if (!result.delivered) continue;
      await markEventNotified(env.DB, event.id, new Date().toISOString());
      delivered += 1;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await markEventFailed(env.DB, event.id, message);
      console.error("Event notification failed", { eventId: event.id, error: message });
    }
  }
  return delivered;
}

export async function runSensorCycle(env, scheduledAt, fetcher = fetch) {
  const startedClock = Date.now();
  const scheduledIso = scheduledAt.toISOString();
  const cycleId = `sensor-${scheduledAt.getTime()}`;
  const startedAt = new Date().toISOString();
  await startCycle(env.DB, cycleId, scheduledIso, startedAt);

  let checkedCount = 0;
  let availableCount = 0;
  let unknownCount = 0;
  let eventCount = 0;
  let skippedCount = 0;
  const results = await mapWithConcurrency(HOTELS, 3, async (hotel) => {
    const decision = await hotelCheckDecision(env.DB, hotel.key, new Date(startedAt));
    if (!decision.shouldCheck) {
      skippedCount += 1;
      return {
        hotelKey: hotel.key,
        status: "backoff",
        failures: decision.failures,
        nextCheckAt: decision.nextCheckAt,
        durationMs: 0,
        event: null,
      };
    }

    const hotelClock = Date.now();
    let observation;
    try {
      observation = await ADAPTERS[hotel.engine](hotel, fetcher);
    } catch (error) {
      observation = unknown(error instanceof Error ? error.message : String(error));
    }
    const durationMs = Date.now() - hotelClock;
    const observedAt = new Date().toISOString();
    const event = await recordObservation(env.DB, {
      cycleId,
      hotel,
      observation,
      durationMs,
      observedAt,
    });
    checkedCount += 1;
    availableCount += observation.status === "available" ? 1 : 0;
    unknownCount += observation.status === "unknown" ? 1 : 0;
    if (event) {
      eventCount += 1;
      if (event.requiresValidation) {
        try {
          await dispatchBrowserValidation(
            env,
            { reason: event.type, hotel_key: hotel.key, cycle_id: cycleId },
            fetcher,
          );
        } catch (error) {
          console.error("Browser validation dispatch failed", {
            hotelKey: hotel.key,
            cycleId,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }
    }
    return { hotelKey: hotel.key, ...observation, durationMs, event: event?.type || null };
  });

  // The hourly browser pass remains a safety net for API schema drift and for
  // Lakeview/SiteMinder, which currently have no reliable lightweight probe.
  if (scheduledAt.getUTCMinutes() === 0) {
    try {
      await dispatchBrowserValidation(
        env,
        { reason: "hourly-watchdog", cycle_id: cycleId },
        fetcher,
      );
    } catch (error) {
      console.error("Hourly browser watchdog dispatch failed", {
        cycleId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }

  let status = "success";
  if (checkedCount === 0 && skippedCount > 0) status = "backoff";
  else if (unknownCount === checkedCount) status = "error";
  else if (unknownCount > 0) status = "partial";
  const notificationCount = await flushNotificationOutbox(env, fetcher);
  await finishCycle(env.DB, cycleId, {
    finishedAt: new Date().toISOString(),
    status,
    checkedCount,
    availableCount,
    unknownCount,
    skippedCount,
    eventCount,
    durationMs: Date.now() - startedClock,
  });
  return {
    cycleId,
    status,
    checkedCount,
    availableCount,
    unknownCount,
    skippedCount,
    eventCount,
    notificationCount,
    results,
  };
}

async function health(db) {
  const latest = await db
    .prepare("SELECT * FROM sensor_cycles ORDER BY scheduled_at DESC LIMIT 1")
    .first();
  return Response.json({ ok: Boolean(latest), latest });
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(runSensorCycle(env, new Date(controller.scheduledTime)));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return health(env.DB);
    }
    if (request.method === "POST" && url.pathname === "/run") {
      const authorization = request.headers.get("authorization");
      if (!env.ADMIN_TOKEN || authorization !== `Bearer ${env.ADMIN_TOKEN}`) {
        return new Response("Unauthorized", { status: 401 });
      }
      return Response.json(await runSensorCycle(env, new Date()));
    }
    return new Response("LakeWatch sensor", { status: 200 });
  },
};
