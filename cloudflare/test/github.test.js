import test from "node:test";
import assert from "node:assert/strict";

import { dispatchBrowserValidation } from "../src/github.js";

test("shadow mode never dispatches browser validation", async () => {
  const result = await dispatchBrowserValidation({ SHADOW_MODE: "true" }, {});
  assert.equal(result, false);
});

test("active mode rejects a missing GitHub token", async () => {
  await assert.rejects(
    dispatchBrowserValidation({ SHADOW_MODE: "false" }, {}),
    /GITHUB_TOKEN is not configured/,
  );
});

test("dispatch payload targets one hotel and one event", async () => {
  let request;
  const fetcher = async (url, options) => {
    request = { url, options };
    return { status: 204 };
  };
  const payload = { hotel_key: "hotel-a", event_id: 7, idempotency_key: "event-7" };
  const result = await dispatchBrowserValidation(
    {
      SHADOW_MODE: "false",
      GITHUB_TOKEN: "secret",
      GITHUB_OWNER: "owner",
      GITHUB_REPO: "repo",
    },
    payload,
    fetcher,
  );
  assert.equal(result, true);
  assert.equal(request.url, "https://api.github.com/repos/owner/repo/dispatches");
  assert.deepEqual(JSON.parse(request.options.body).client_payload, payload);
});
