import test from "node:test";
import assert from "node:assert/strict";

import { BROWSER_ONLY_HOTELS, HOTELS, hotelByKey } from "../src/hotels.js";

test("shared hotel configuration routes five API and two browser hotels", () => {
  assert.equal(HOTELS.length, 5);
  assert.deepEqual([...BROWSER_ONLY_HOTELS].sort(), ["galaxy-boutique", "lakeview-tekapo"]);
  assert.equal(hotelByKey("tasman-hahei-beach").checkIn, "2027-02-12");
  assert.equal(hotelByKey("missing"), null);
});
