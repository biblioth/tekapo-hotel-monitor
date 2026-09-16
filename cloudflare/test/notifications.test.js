import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPushPlusTitle,
  configuredNotificationChannels,
  renderAlert,
  sendEventNotifications,
  sendTextNotificationChannel,
} from "../src/notifications.js";

const event = {
  type: "availability_returned",
  payload: {
    hotelName: "Peppers Bluewater Resort Lake Tekapo",
    checkIn: "2027-02-05",
    checkOut: "2027-02-06",
    offers: [
      {
        roomName: "Deluxe Lake View Room",
        priceLabel: "NZ$420",
        freeCancellation: true,
        freeCancellationUntilDate: "2027-02-03",
        link: "https://example.com/book",
      },
    ],
  },
};

test("renders the same concise alert used by the browser monitor", () => {
  assert.equal(
    renderAlert(event),
    [
      "🔔 Peppers Bluewater 重新有房",
      "2027/2/5–2027/2/6 · Deluxe Lake View Room",
      "NZ$420 · 免费取消至 2027/2/3",
      "立即预订：https://example.com/book",
    ].join("\n"),
  );
  assert.equal(
    buildPushPlusTitle(event),
    "🔔 Peppers Bluewater｜Deluxe Lake View Room｜NZ$420",
  );
});

test("omits details the official API did not disclose", () => {
  const withoutDetails = structuredClone(event);
  withoutDetails.type = "new_room";
  withoutDetails.payload.offers[0].priceLabel = null;
  withoutDetails.payload.offers[0].freeCancellation = false;

  assert.equal(
    renderAlert(withoutDetails),
    [
      "🔔 Peppers Bluewater 新增房型",
      "2027/2/5–2027/2/6 · Deluxe Lake View Room",
      "立即预订：https://example.com/book",
    ].join("\n"),
  );
});

test("delivers to Feishu and PushPlus without emitting a separate fault alert", async () => {
  const requests = [];
  const fetcher = async (url, options) => {
    requests.push([url, JSON.parse(options.body)]);
    return {
      ok: true,
      status: 200,
      async json() {
        return String(url).includes("pushplus") ? { code: 200 } : { code: 0 };
      },
    };
  };

  const result = await sendEventNotifications(
    {
      FEISHU_WEBHOOK_URL: "https://example.com/feishu",
      PUSHPLUS_TOKEN: "token",
      PUSHPLUS_TOPIC: "lakewatch",
    },
    event,
    fetcher,
  );

  assert.equal(result.delivered, true);
  assert.equal(requests.length, 2);
  assert.equal(requests[0][1].content.text, renderAlert(event));
  assert.equal(requests[1][1].title, buildPushPlusTitle(event));
});

test("queues can deliver one channel without changing the other channel", async () => {
  const requests = [];
  const fetcher = async (url, options) => {
    requests.push([url, JSON.parse(options.body)]);
    return { ok: true, status: 200, async json() { return { code: 0 }; } };
  };
  const env = {
    FEISHU_WEBHOOK_URL: "https://example.com/feishu",
    PUSHPLUS_TOKEN: "token",
  };
  assert.deepEqual(configuredNotificationChannels(env), ["feishu", "pushplus"]);
  await sendTextNotificationChannel(env, "日报正文", "日报标题", "feishu", fetcher);
  assert.equal(requests.length, 1);
  assert.equal(requests[0][1].content.text, "日报正文");
});
