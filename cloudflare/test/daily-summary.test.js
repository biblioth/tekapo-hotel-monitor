import test from "node:test";
import assert from "node:assert/strict";

import { dailyCycleCoverage, previousShanghaiDay } from "../src/index.js";

test("00:07 Asia/Shanghai summarizes the preceding local calendar day", () => {
  const day = previousShanghaiDay(new Date("2026-09-16T16:07:00Z"));
  assert.deepEqual(day, {
    label: "2026-09-16",
    start: "2026-09-15T16:00:00.000Z",
    end: "2026-09-16T16:00:00.000Z",
  });
});

test("activation day expects only cycles after the first production cycle", () => {
  const day = previousShanghaiDay(new Date("2026-09-16T16:07:00Z"));
  const coverage = dailyCycleCoverage(day, {
    total: 101,
    first_cycle_ever_at: "2026-09-16T07:35:35.000Z",
    first_cycle_at: "2026-09-16T07:35:35.000Z",
    last_cycle_at: "2026-09-16T15:55:35.000Z",
  });

  assert.deepEqual(coverage, {
    expected: 101,
    missing: 0,
    ratio: 1,
    isActivationDay: true,
    hasGap: false,
    firstClock: "15:35",
    lastClock: "23:55",
  });
});

test("later days still detect material gaps against all 288 cycles", () => {
  const day = previousShanghaiDay(new Date("2026-09-17T16:07:00Z"));
  const coverage = dailyCycleCoverage(day, {
    total: 270,
    first_cycle_ever_at: "2026-09-16T07:35:35.000Z",
    first_cycle_at: "2026-09-16T16:00:35.000Z",
    last_cycle_at: "2026-09-17T15:55:35.000Z",
  });

  assert.equal(coverage.expected, 288);
  assert.equal(coverage.missing, 18);
  assert.equal(coverage.isActivationDay, false);
  assert.equal(coverage.hasGap, true);
  assert.equal(Math.round(coverage.ratio * 100), 94);
});
