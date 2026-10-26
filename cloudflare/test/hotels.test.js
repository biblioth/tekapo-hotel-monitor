import test from "node:test";
import assert from "node:assert/strict";

import { BROWSER_ONLY_HOTELS, HOTELS, hotelByKey } from "../src/hotels.js";

test("shared hotel configuration routes six API watches and two browser hotels", () => {
  assert.equal(HOTELS.length, 6);
  assert.deepEqual([...BROWSER_ONLY_HOTELS].sort(), ["galaxy-boutique", "lakeview-tekapo"]);
  assert.equal(hotelByKey("tasman-hahei-beach").checkIn, "2027-02-12");
  const hermitage = HOTELS.filter((hotel) => hotel.key.startsWith("hermitage-mt-cook-"));
  assert.deepEqual(
    hermitage.map((hotel) => [hotel.checkIn, hotel.checkOut]),
    [
      ["2027-02-05", "2027-02-06"],
      ["2027-02-06", "2027-02-07"],
    ],
  );
  assert.ok(hermitage.every((hotel) => hotel.roomCodes.join(",") === "MFQQ,MFQSS"));
  assert.equal(hotelByKey("missing"), null);
});
