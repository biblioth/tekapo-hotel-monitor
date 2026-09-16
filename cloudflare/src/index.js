import { checkAccor } from "./adapters/accor.js";
import { checkAgilysys } from "./adapters/agilysys.js";
import { checkIbex } from "./adapters/ibex.js";
import { checkNewbook } from "./adapters/newbook.js";
import { checkStaah } from "./adapters/staah.js";
import { dispatchBrowserValidation } from "./github.js";
import { BROWSER_ONLY_HOTELS, HOTELS, hotelByKey } from "./hotels.js";
import { unknown } from "./http.js";
import {
  configuredNotificationChannels,
  sendNotificationChannel,
  sendTextNotificationChannel,
} from "./notifications.js";
import {
  cleanupSensorData,
  completeValidation,
  ensureDeliveries,
  ensureSummaryDeliveries,
  finishCycle,
  getDelivery,
  getSummaryDelivery,
  hotelCheckDecision,
  markDeliveryDelivered,
  markDeliveryFailed,
  markDeliveryQueued,
  markSummaryDeliveryDelivered,
  markSummaryDeliveryFailed,
  markSummaryDeliveryQueued,
  markValidationDispatched,
  markValidationDispatchFailed,
  pendingConfirmedEvents,
  pendingDeliveries,
  pendingSummaryDeliveries,
  pendingValidations,
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
const VALIDATION_RETRY_MS = 15 * 60_000;
const DELIVERY_REQUEUE_MS = 60 * 60_000;
const HEALTH_SENSOR_MAX_AGE_MS = 15 * 60_000;
const HEALTH_BROWSER_MAX_AGE_MS = 150 * 60_000;
const HEALTH_VALIDATION_MAX_AGE_MS = 45 * 60_000;
const HEALTH_DELIVERY_MAX_AGE_MS = 2 * 60 * 60_000;
const RETENTION_DAYS = 90;
const DAILY_SUMMARY_CRON = "7 16 * * *";

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

async function dispatchPendingValidations(env, fetcher) {
  if (env.SHADOW_MODE === "true") return 0;
  const staleBefore = new Date(Date.now() - VALIDATION_RETRY_MS).toISOString();
  let dispatched = 0;
  for (const event of await pendingValidations(env.DB, staleBefore)) {
    try {
      await dispatchBrowserValidation(
        env,
        {
          reason: event.type,
          event_id: event.id,
          idempotency_key: event.idempotencyKey,
          hotel_key: event.hotelKey,
        },
        fetcher,
      );
      await markValidationDispatched(env.DB, event.id, new Date().toISOString());
      dispatched += 1;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await markValidationDispatchFailed(env.DB, event.id, message);
      console.error("Browser validation dispatch failed", { eventId: event.id, error: message });
    }
  }
  return dispatched;
}

async function dispatchBrowserOnlyWatchdog(env, cycleId, fetcher) {
  if (env.SHADOW_MODE === "true") return 0;
  let dispatched = 0;
  for (const hotelKey of BROWSER_ONLY_HOTELS) {
    try {
      await dispatchBrowserValidation(
        env,
        { reason: "browser-only-watchdog", hotel_key: hotelKey, cycle_id: cycleId },
        fetcher,
      );
      dispatched += 1;
    } catch (error) {
      console.error("Browser-only watchdog dispatch failed", {
        hotelKey,
        cycleId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return dispatched;
}

async function enqueuePendingNotifications(env) {
  if (env.SHADOW_MODE === "true") return 0;
  const channels = configuredNotificationChannels(env);
  if (!channels.length) return 0;
  const now = new Date().toISOString();
  for (const event of await pendingConfirmedEvents(env.DB)) {
    await ensureDeliveries(env.DB, event.id, channels, now);
  }

  const staleBefore = new Date(Date.now() - DELIVERY_REQUEUE_MS).toISOString();
  let enqueued = 0;
  for (const delivery of await pendingDeliveries(env.DB, staleBefore)) {
    try {
      await env.NOTIFICATION_QUEUE.send({ deliveryId: delivery.deliveryId });
      await markDeliveryQueued(env.DB, delivery.deliveryId, new Date().toISOString());
      enqueued += 1;
    } catch (error) {
      console.error("Notification enqueue failed", {
        deliveryId: delivery.deliveryId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  for (const delivery of await pendingSummaryDeliveries(env.DB, staleBefore)) {
    try {
      await env.NOTIFICATION_QUEUE.send({
        kind: "summary",
        deliveryId: delivery.deliveryId,
      });
      await markSummaryDeliveryQueued(env.DB, delivery.deliveryId, new Date().toISOString());
      enqueued += 1;
    } catch (error) {
      console.error("Daily summary enqueue failed", {
        deliveryId: delivery.deliveryId,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return enqueued;
}

export async function runSensorCycle(env, scheduledAt, fetcher = fetch) {
  const startedClock = Date.now();
  const scheduledIso = scheduledAt.toISOString();
  const cycleId = `sensor-${scheduledAt.getTime()}`;
  const startedAt = new Date().toISOString();
  if (!(await startCycle(env.DB, cycleId, scheduledIso, startedAt))) {
    return { cycleId, status: "duplicate", results: [] };
  }

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
    eventCount += event?.created ? 1 : 0;
    return { hotelKey: hotel.key, ...observation, durationMs, event: event?.type || null };
  });

  const validationDispatchCount = await dispatchPendingValidations(env, fetcher);
  let watchdogDispatchCount = 0;
  if (scheduledAt.getUTCMinutes() === 0) {
    watchdogDispatchCount = await dispatchBrowserOnlyWatchdog(env, cycleId, fetcher);
  }

  let status = "success";
  if (checkedCount === 0 && skippedCount > 0) status = "backoff";
  else if (unknownCount === checkedCount) status = "error";
  else if (unknownCount > 0) status = "partial";
  const notificationEnqueueCount = await enqueuePendingNotifications(env);
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
  if (scheduledAt.getUTCHours() === 0 && scheduledAt.getUTCMinutes() === 0) {
    const cutoff = new Date(Date.now() - RETENTION_DAYS * 86_400_000).toISOString();
    await cleanupSensorData(env.DB, cutoff);
  }
  return {
    cycleId,
    status,
    checkedCount,
    availableCount,
    unknownCount,
    skippedCount,
    eventCount,
    validationDispatchCount,
    watchdogDispatchCount,
    notificationEnqueueCount,
    results,
  };
}

export function previousShanghaiDay(scheduledAt) {
  const local = new Date(scheduledAt.getTime() + 8 * 60 * 60_000);
  local.setUTCDate(local.getUTCDate() - 1);
  const year = local.getUTCFullYear();
  const month = local.getUTCMonth();
  const day = local.getUTCDate();
  const start = new Date(Date.UTC(year, month, day) - 8 * 60 * 60_000);
  const end = new Date(start.getTime() + 86_400_000);
  const label = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  return { label, start: start.toISOString(), end: end.toISOString() };
}

export async function runDailySummary(env, scheduledAt = new Date()) {
  if (env.SHADOW_MODE === "true") return { status: "shadow-skipped" };
  const day = previousShanghaiDay(scheduledAt);
  const cycles = await env.DB
    .prepare(
      `SELECT COUNT(*) AS total,
              SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successful,
              SUM(CASE WHEN status='partial' THEN 1 ELSE 0 END) AS partial,
              SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
              SUM(checked_count) AS checks,
              SUM(unknown_count) AS unknowns
       FROM sensor_cycles
       WHERE id LIKE 'sensor-%' AND scheduled_at>=? AND scheduled_at<?`,
    )
    .bind(day.start, day.end)
    .first();
  const events = await env.DB
    .prepare(
      `SELECT SUM(CASE
                    WHEN requires_validation=0
                     AND validation_status IN ('not_required', 'confirmed') THEN 1
                    ELSE 0
                  END) AS changes,
              SUM(CASE WHEN notified_at IS NOT NULL THEN 1 ELSE 0 END) AS notified,
              SUM(CASE WHEN validation_status='rejected' THEN 1 ELSE 0 END) AS rejected
       FROM sensor_events WHERE created_at>=? AND created_at<?`,
    )
    .bind(day.start, day.end)
    .first();
  const failures = await env.DB
    .prepare(
      `SELECT hotel_key, COUNT(*) AS count
       FROM sensor_observations
       WHERE observed_at>=? AND observed_at<? AND status='unknown'
       GROUP BY hotel_key ORDER BY count DESC`,
    )
    .bind(day.start, day.end)
    .all();

  const total = Number(cycles?.total || 0);
  const unknowns = Number(cycles?.unknowns || 0);
  const changes = Number(events?.changes || 0);
  const notified = Number(events?.notified || 0);
  let conclusion = "✅ 高频监控正常｜未发现新房";
  if (total === 0) conclusion = "🚨 昨日 Cloudflare 监控未运行";
  else if (changes > 0) conclusion = `🔔 发现 ${changes} 次房态变化｜已完成 ${notified} 次提醒`;
  else if (total < 276 || unknowns > 0) conclusion = "⚠️ 监控有缺口或异常｜未发现新房";
  const lines = [
    `📊 LakeWatch 日报｜${day.label}`,
    conclusion,
    `传感器周期 ${total}/288 次｜酒店探测 ${Number(cycles?.checks || 0)} 次`,
  ];
  if (unknowns > 0) {
    const detail = (failures.results || [])
      .map((row) => `${row.hotel_key} ${row.count} 次`)
      .join("；");
    lines.push(`接口异常 ${unknowns} 次${detail ? `｜${detail}` : ""}`);
  }
  if (Number(events?.rejected || 0) > 0) {
    lines.push(`浏览器驳回候选 ${Number(events.rejected)} 次`);
  }
  const message = lines.join("\n");
  const channels = configuredNotificationChannels(env);
  await ensureSummaryDeliveries(
    env.DB,
    day.label,
    channels,
    message,
    lines.slice(0, 2).join("｜").slice(0, 80),
    new Date().toISOString(),
  );
  const notificationEnqueueCount = await enqueuePendingNotifications(env);
  return { status: "queued", date: day.label, channels, notificationEnqueueCount, message };
}

function validationError(message, status = 400) {
  return Response.json({ error: message }, { status });
}

export async function receiveValidation(request, env) {
  const authorization = request.headers.get("authorization");
  if (!env.VALIDATION_TOKEN || authorization !== `Bearer ${env.VALIDATION_TOKEN}`) {
    return new Response("Unauthorized", { status: 401 });
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return validationError("Invalid JSON");
  }
  if (!body.hotel_key || !body.observation?.status) {
    return validationError("hotel_key and observation.status are required");
  }
  const statuses = new Set(["available", "unavailable", "unknown", "error"]);
  if (!statuses.has(body.observation.status)) {
    return validationError("observation.status is invalid");
  }
  if (body.observation.offers != null && !Array.isArray(body.observation.offers)) {
    return validationError("observation.offers must be an array");
  }
  if ((body.observation.offers || []).length > 100) {
    return validationError("observation.offers exceeds the 100-item limit", 413);
  }
  const hasEventId = body.event_id !== null && body.event_id !== undefined && body.event_id !== "";
  const hasIdempotencyKey = Boolean(body.idempotency_key);
  if (hasEventId !== hasIdempotencyKey) {
    return validationError("event_id and idempotency_key must be provided together");
  }
  if (hasEventId && (!Number.isInteger(Number(body.event_id)) || Number(body.event_id) < 1)) {
    return validationError("event_id must be a positive integer");
  }
  const observation = {
    status: body.observation.status === "error" ? "unknown" : body.observation.status,
    confidence: "confirmed",
    offers: Array.isArray(body.observation.offers) ? body.observation.offers : [],
    message: body.observation.message || null,
  };
  const completedAt = new Date().toISOString();
  let result;
  if (hasEventId) {
    result = await completeValidation(env.DB, {
      eventId: Number(body.event_id),
      idempotencyKey: String(body.idempotency_key),
      hotelKey: String(body.hotel_key),
      observation,
      completedAt,
    });
  } else {
    const hotelKey = String(body.hotel_key);
    if (!BROWSER_ONLY_HOTELS.includes(hotelKey)) {
      return validationError("Eventless callbacks are restricted to browser-only hotels");
    }
    const hotel = hotelByKey(hotelKey);
    if (!hotel) return validationError("Unknown hotel_key", 404);
    const sourceCycleId = body.cycle_id ? String(body.cycle_id).trim() : null;
    if (sourceCycleId && sourceCycleId.length > 160) {
      return validationError("cycle_id exceeds the 160-character limit", 413);
    }
    const cycleId = sourceCycleId
      ? `browser-${hotelKey}-${sourceCycleId}`
      : `browser-${hotelKey}-${Date.now()}`;
    if (!(await startCycle(env.DB, cycleId, completedAt, completedAt))) {
      return Response.json({
        ok: true,
        result: { duplicate: true, cycleId },
        notificationEnqueueCount: 0,
      });
    }
    const event = await recordObservation(env.DB, {
      cycleId,
      hotel,
      observation,
      durationMs: Number(body.duration_ms || 0),
      observedAt: completedAt,
    });
    await finishCycle(env.DB, cycleId, {
      finishedAt: completedAt,
      status: observation.status === "unknown" ? "error" : "success",
      checkedCount: 1,
      availableCount: observation.status === "available" ? 1 : 0,
      unknownCount: observation.status === "unknown" ? 1 : 0,
      skippedCount: 0,
      eventCount: event?.created ? 1 : 0,
      durationMs: Number(body.duration_ms || 0),
    });
    result = event || { confirmed: true, baseline: true };
  }
  const notificationEnqueueCount = await enqueuePendingNotifications(env);
  return Response.json({ ok: true, result, notificationEnqueueCount });
}

export async function health(env) {
  const latest = await env.DB
    .prepare(
      "SELECT * FROM sensor_cycles WHERE id LIKE 'sensor-%' ORDER BY scheduled_at DESC LIMIT 1",
    )
    .first();
  const ageMs = latest ? Date.now() - new Date(latest.scheduled_at).getTime() : null;
  const sensorHealthy =
    Boolean(latest?.finished_at) &&
    Number.isFinite(ageMs) &&
    ageMs >= 0 &&
    ageMs < HEALTH_SENSOR_MAX_AGE_MS &&
    latest.status !== "error";
  const pending = await env.DB
    .prepare(
      `SELECT
         SUM(CASE
               WHEN requires_validation=1
                AND validation_status IN ('pending', 'dispatched') THEN 1
               ELSE 0
             END) AS validations,
         SUM(CASE WHEN notified_at IS NULL AND requires_validation=0 THEN 1 ELSE 0 END) AS notifications
       FROM sensor_events`,
    )
    .first();
  const now = Date.now();
  const staleValidationBefore = new Date(now - HEALTH_VALIDATION_MAX_AGE_MS).toISOString();
  const staleDeliveryBefore = new Date(now - HEALTH_DELIVERY_MAX_AGE_MS).toISOString();
  const stale = await env.DB
    .prepare(
      `SELECT
         (SELECT COUNT(*) FROM sensor_events
          WHERE requires_validation=1
            AND validation_status IN ('pending', 'dispatched')
            AND created_at < ?) AS validations,
         (SELECT COUNT(*) FROM sensor_deliveries
          WHERE delivered_at IS NULL AND created_at < ?) AS deliveries,
         (SELECT COUNT(*) FROM sensor_summary_deliveries
          WHERE delivered_at IS NULL AND created_at < ?) AS summaries`,
    )
    .bind(staleValidationBefore, staleDeliveryBefore, staleDeliveryBefore)
    .first();
  const browserRows = BROWSER_ONLY_HOTELS.length
    ? await env.DB
        .prepare(
          `SELECT hotel_key, MAX(observed_at) AS observed_at
           FROM sensor_observations
           WHERE hotel_key IN (${BROWSER_ONLY_HOTELS.map(() => "?").join(",")})
           GROUP BY hotel_key`,
        )
        .bind(...BROWSER_ONLY_HOTELS)
        .all()
    : { results: [] };
  const browserTimes = new Map(
    (browserRows.results || []).map((row) => [row.hotel_key, row.observed_at]),
  );
  const browserOnly = BROWSER_ONLY_HOTELS.map((hotelKey) => {
    const observedAt = browserTimes.get(hotelKey) || null;
    const browserAgeMs = observedAt ? now - new Date(observedAt).getTime() : null;
    return {
      hotelKey,
      observedAt,
      ageMs: browserAgeMs,
      ok:
        Number.isFinite(browserAgeMs) &&
        browserAgeMs >= 0 &&
        browserAgeMs < HEALTH_BROWSER_MAX_AGE_MS,
    };
  });
  const shadow = env.SHADOW_MODE === "true";
  const channels = configuredNotificationChannels(env);
  const activeHealthy =
    browserOnly.every((entry) => entry.ok) &&
    channels.length > 0 &&
    Number(stale?.validations || 0) === 0 &&
    Number(stale?.deliveries || 0) === 0 &&
    Number(stale?.summaries || 0) === 0;
  const healthy = sensorHealthy && (shadow || activeHealthy);
  return Response.json(
    {
      ok: healthy,
      mode: shadow ? "shadow" : "active",
      components: {
        sensor: { ok: sensorHealthy, latest, ageMs },
        browserOnly: { ok: shadow || browserOnly.every((entry) => entry.ok), hotels: browserOnly },
        notifications: {
          ok:
            shadow ||
            (channels.length > 0 &&
              Number(stale?.deliveries || 0) === 0 &&
              Number(stale?.summaries || 0) === 0),
          channels,
        },
        validations: { ok: shadow || Number(stale?.validations || 0) === 0 },
      },
      pending: pending || { validations: 0, notifications: 0 },
      stale: stale || { validations: 0, deliveries: 0, summaries: 0 },
    },
    { status: healthy ? 200 : 503 },
  );
}

async function consumeNotificationBatch(batch, env) {
  for (const message of batch.messages) {
    if (message.body?.kind === "summary") {
      const deliveryId = Number(message.body?.deliveryId);
      const delivery = Number.isFinite(deliveryId)
        ? await getSummaryDelivery(env.DB, deliveryId)
        : null;
      if (!delivery || delivery.deliveredAt) {
        message.ack();
        continue;
      }
      try {
        await sendTextNotificationChannel(
          env,
          delivery.message,
          delivery.title,
          delivery.channel,
        );
        await markSummaryDeliveryDelivered(env.DB, delivery.deliveryId, new Date().toISOString());
        message.ack();
      } catch (error) {
        const messageText = error instanceof Error ? error.message : String(error);
        await markSummaryDeliveryFailed(
          env.DB,
          delivery.deliveryId,
          messageText,
          new Date().toISOString(),
        );
        const delaySeconds = Math.min(3600, 30 * 2 ** Math.min(delivery.attempts, 7));
        message.retry({ delaySeconds });
      }
      continue;
    }
    const deliveryId = Number(message.body?.deliveryId);
    const delivery = Number.isFinite(deliveryId) ? await getDelivery(env.DB, deliveryId) : null;
    if (!delivery || delivery.deliveredAt) {
      message.ack();
      continue;
    }
    try {
      await sendNotificationChannel(env, delivery.event, delivery.channel);
      await markDeliveryDelivered(
        env.DB,
        delivery.deliveryId,
        delivery.eventId,
        new Date().toISOString(),
      );
      message.ack();
    } catch (error) {
      const messageText = error instanceof Error ? error.message : String(error);
      await markDeliveryFailed(env.DB, delivery.deliveryId, messageText, new Date().toISOString());
      const delaySeconds = Math.min(3600, 30 * 2 ** Math.min(delivery.attempts, 7));
      message.retry({ delaySeconds });
    }
  }
}

export default {
  async scheduled(controller, env, ctx) {
    const scheduledAt = new Date(controller.scheduledTime);
    if (controller.cron === DAILY_SUMMARY_CRON) {
      ctx.waitUntil(runDailySummary(env, scheduledAt));
    } else {
      ctx.waitUntil(runSensorCycle(env, scheduledAt));
    }
  },

  async queue(batch, env) {
    await consumeNotificationBatch(batch, env);
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return health(env);
    }
    if (request.method === "POST" && url.pathname === "/validation") {
      return receiveValidation(request, env);
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
