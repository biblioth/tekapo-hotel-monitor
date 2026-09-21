import test from "node:test";
import assert from "node:assert/strict";

import { backoffDecision, backoffDelayMs } from "../src/backoff.js";

test("uses bounded 15, 30, and 60 minute error backoff", () => {
  assert.equal(backoffDelayMs(0), 0);
  assert.equal(backoffDelayMs(1), 15 * 60_000);
  assert.equal(backoffDelayMs(2), 30 * 60_000);
  assert.equal(backoffDelayMs(3), 60 * 60_000);
  assert.equal(backoffDelayMs(20), 60 * 60_000);
});

test("skips only the failing hotel until its retry time", () => {
  const snapshot = {
    observed_at: "2026-09-16T00:00:00.000Z",
    consecutive_unknown: 1,
  };

  assert.deepEqual(backoffDecision(snapshot, new Date("2026-09-16T00:10:00.000Z")), {
    shouldCheck: false,
    failures: 1,
    nextCheckAt: "2026-09-16T00:15:00.000Z",
  });
  assert.deepEqual(backoffDecision(snapshot, new Date("2026-09-16T00:15:00.000Z")), {
    shouldCheck: true,
    failures: 1,
    nextCheckAt: "2026-09-16T00:15:00.000Z",
  });
});

test("a successful observation removes all backoff", () => {
  const decision = backoffDecision(
    { observed_at: "2026-09-16T00:00:00.000Z", consecutive_unknown: 0 },
    new Date("2026-09-16T00:01:00.000Z"),
  );
  assert.equal(decision.shouldCheck, true);
  assert.equal(decision.nextCheckAt, null);
});
