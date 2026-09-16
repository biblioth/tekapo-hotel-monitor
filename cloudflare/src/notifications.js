const HOTEL_SHORT_NAMES = {
  "Ranginui at Lake Tekapo": "Ranginui",
  "Lakeview Tekapo": "Lakeview",
  "Grand Suites Lake Tekapo": "Grand Suites",
  "Galaxy Boutique Hotel": "Galaxy Boutique",
  "Peppers Bluewater Resort Lake Tekapo": "Peppers Bluewater",
  "The Hermitage Hotel Mt Cook": "Hermitage Mt Cook",
  "Tasman Holiday Parks Hahei Beach": "Hahei Beach",
};

function shortDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
  if (!match) return String(value);
  return `${Number(match[1])}/${Number(match[2])}/${Number(match[3])}`;
}

function cancellationText(offer) {
  if (!offer.freeCancellation) return null;
  if (!offer.freeCancellationUntilDate) return "可免费取消";
  const time = offer.freeCancellationUntilTime ? ` ${offer.freeCancellationUntilTime}` : "";
  return `免费取消至 ${shortDate(offer.freeCancellationUntilDate)}${time}`;
}

export function renderAlert(event) {
  const { payload } = event;
  const offers = payload.offers;
  const first = offers[0];
  const hotel = HOTEL_SHORT_NAMES[payload.hotelName] || payload.hotelName;
  let headline;
  if (event.type === "availability_returned") {
    headline = offers.length === 1 ? "重新有房" : `重新有房（${offers.length} 个房型）`;
  } else {
    headline = offers.length === 1 ? "新增房型" : `新增 ${offers.length} 个房型`;
  }

  const stay = `${shortDate(payload.checkIn)}–${shortDate(payload.checkOut)}`;
  const lines = [`🔔 ${hotel} ${headline}`];
  if (offers.length === 1) {
    lines.push(`${stay} · ${first.roomName}`);
    const details = [first.priceLabel, cancellationText(first)].filter(Boolean);
    if (details.length) lines.push(details.join(" · "));
  } else {
    lines.push(`${stay}${first.priceLabel ? ` · 最低 ${first.priceLabel}` : ""}`);
    let rooms = offers
      .slice(0, 3)
      .map((offer) => offer.roomName)
      .join("、");
    if (offers.length > 3) rooms += `等 ${offers.length} 个房型`;
    lines.push(rooms);
  }
  if (first.link) lines.push(`立即预订：${first.link}`);
  return lines.join("\n");
}

export function buildPushPlusTitle(event) {
  const { payload } = event;
  const offers = payload.offers;
  const first = offers[0];
  const hotel = HOTEL_SHORT_NAMES[payload.hotelName] || payload.hotelName;
  let detail = offers.length > 1 ? `新增 ${offers.length} 个房型` : first.roomName;
  if (detail.length > 24) detail = `${detail.slice(0, 23)}…`;
  return [`🔔 ${hotel}`, detail, first.priceLabel].filter(Boolean).join("｜").slice(0, 80);
}

async function feishuSignature(secret, timestamp) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(`${timestamp}\n${secret}`),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, new Uint8Array());
  return btoa(String.fromCharCode(...new Uint8Array(signature)));
}

async function sendFeishu(env, message, fetcher) {
  const payload = { msg_type: "text", content: { text: message } };
  if (env.FEISHU_WEBHOOK_SECRET) {
    payload.timestamp = String(Math.floor(Date.now() / 1000));
    payload.sign = await feishuSignature(env.FEISHU_WEBHOOK_SECRET, payload.timestamp);
  }
  const response = await fetcher(env.FEISHU_WEBHOOK_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`Feishu returned HTTP ${response.status}`);
  const result = await response.json();
  const code = result.code ?? result.StatusCode ?? 0;
  if (![0, "0", null].includes(code)) throw new Error(`Feishu rejected the message: ${code}`);
}

async function sendPushPlus(env, event, message, fetcher) {
  const payload = {
    token: env.PUSHPLUS_TOKEN,
    title: buildPushPlusTitle(event),
    content: message,
    template: "txt",
    channel: "wechat",
  };
  if (env.PUSHPLUS_TOPIC) payload.topic = env.PUSHPLUS_TOPIC;
  const response = await fetcher("https://www.pushplus.plus/send", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`PushPlus returned HTTP ${response.status}`);
  const result = await response.json();
  if (![200, "200"].includes(result.code)) throw new Error(`PushPlus rejected the message: ${result.code}`);
}

async function sendPushPlusText(env, message, title, fetcher) {
  const payload = {
    token: env.PUSHPLUS_TOKEN,
    title: String(title || "LakeWatch").slice(0, 80),
    content: message,
    template: "txt",
    channel: "wechat",
  };
  if (env.PUSHPLUS_TOPIC) payload.topic = env.PUSHPLUS_TOPIC;
  const response = await fetcher("https://www.pushplus.plus/send", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`PushPlus returned HTTP ${response.status}`);
  const result = await response.json();
  if (![200, "200"].includes(result.code)) {
    throw new Error(`PushPlus rejected the message: ${result.code}`);
  }
}

export function configuredNotificationChannels(env) {
  return [
    env.FEISHU_WEBHOOK_URL ? "feishu" : null,
    env.PUSHPLUS_TOKEN ? "pushplus" : null,
  ].filter(Boolean);
}

export async function sendNotificationChannel(env, event, channel, fetcher = fetch) {
  const message = renderAlert(event);
  if (channel === "feishu") {
    if (!env.FEISHU_WEBHOOK_URL) throw new Error("Feishu is not configured");
    await sendFeishu(env, message, fetcher);
    return;
  }
  if (channel === "pushplus") {
    if (!env.PUSHPLUS_TOKEN) throw new Error("PushPlus is not configured");
    await sendPushPlus(env, event, message, fetcher);
    return;
  }
  throw new Error(`Unsupported notification channel: ${channel}`);
}

export async function sendTextNotificationChannel(
  env,
  message,
  title,
  channel,
  fetcher = fetch,
) {
  if (channel === "feishu") {
    if (!env.FEISHU_WEBHOOK_URL) throw new Error("Feishu is not configured");
    await sendFeishu(env, message, fetcher);
    return;
  }
  if (channel === "pushplus") {
    if (!env.PUSHPLUS_TOKEN) throw new Error("PushPlus is not configured");
    await sendPushPlusText(env, message, title, fetcher);
    return;
  }
  throw new Error(`Unsupported notification channel: ${channel}`);
}

// Retained for local tests and callers outside the Queue consumer. Production
// delivery uses sendNotificationChannel so each channel has independent state.
export async function sendEventNotifications(env, event, fetcher = fetch) {
  const channels = configuredNotificationChannels(env);
  if (!channels.length) return { delivered: false, failures: [] };
  const results = await Promise.allSettled(
    channels.map((channel) => sendNotificationChannel(env, event, channel, fetcher)),
  );
  const failures = results.flatMap((result, index) =>
    result.status === "rejected"
      ? [`${channels[index]}: ${result.reason instanceof Error ? result.reason.message : result.reason}`]
      : [],
  );
  if (failures.length === channels.length) {
    throw new Error(`All notification channels failed: ${failures.join("; ")}`);
  }
  return { delivered: true, failures };
}
