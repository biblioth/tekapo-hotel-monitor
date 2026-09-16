import test from "node:test";
import assert from "node:assert/strict";

import { applyObservation } from "../src/state-machine.js";

const roomA = { identity: "a", roomName: "Room A" };
const roomB = { identity: "b", roomName: "Room B" };

test("first definite result establishes a quiet baseline", () => {
  const result = applyObservation(null, { status: "available", offers: [roomA] });
  assert.equal(result.event, null);
  assert.equal(result.snapshot.status, "available");
});

test("unavailable to available creates an actionable event", () => {
  const result = applyObservation(
    { status: "unavailable", offers: [], consecutiveUnknown: 0 },
    { status: "available", offers: [roomA] },
  );
  assert.equal(result.event.type, "availability_returned");
  assert.deepEqual(result.event.offers, [roomA]);
});

test("only newly appearing rooms create a new-room event", () => {
  const result = applyObservation(
    { status: "available", offers: [roomA], consecutiveUnknown: 0 },
    { status: "available", offers: [roomA, roomB] },
  );
  assert.equal(result.event.type, "new_room");
  assert.deepEqual(result.event.offers, [roomB]);
});

test("unknown never erases the last definite state", () => {
  const result = applyObservation(
    { status: "available", offers: [roomA], consecutiveUnknown: 1 },
    { status: "unknown", offers: [] },
  );
  assert.equal(result.event, null);
  assert.equal(result.snapshot.status, "available");
  assert.deepEqual(result.snapshot.offers, [roomA]);
  assert.equal(result.snapshot.consecutiveUnknown, 2);
});

test("recovery after an unknown does not look like a new release", () => {
  const afterError = applyObservation(
    { status: "available", offers: [roomA], consecutiveUnknown: 0 },
    { status: "unknown", offers: [] },
  ).snapshot;
  const recovered = applyObservation(afterError, { status: "available", offers: [roomA] });
  assert.equal(recovered.event, null);
});
