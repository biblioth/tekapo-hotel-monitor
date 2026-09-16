import test from "node:test";
import assert from "node:assert/strict";

import { previousShanghaiDay } from "../src/index.js";

test("00:07 Asia/Shanghai summarizes the preceding local calendar day", () => {
  const day = previousShanghaiDay(new Date("2026-09-16T16:07:00Z"));
  assert.deepEqual(day, {
    label: "2026-09-16",
    start: "2026-09-15T16:00:00.000Z",
    end: "2026-09-16T16:00:00.000Z",
  });
});
