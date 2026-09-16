import test from "node:test";
import assert from "node:assert/strict";

import { parseAccor } from "../src/adapters/accor.js";
import { parseAgilysys } from "../src/adapters/agilysys.js";
import { parseIbex } from "../src/adapters/ibex.js";
import { parseNewbook } from "../src/adapters/newbook.js";
import { parseStaah } from "../src/adapters/staah.js";

const hotel = {
  checkIn: "2027-02-05",
  checkOut: "2027-02-06",
  adults: 2,
  propertyId: "property",
  bookingUrl: "https://example.com",
};

test("STAah inventory is a browser-validation candidate", () => {
  const result = parseStaah(
    { PropertyList: [{ DayRate: { "2027-02-05": { Inventory: 1, Rate: 600 } } }] },
    hotel,
  );
  assert.equal(result.status, "available");
  assert.equal(result.confidence, "candidate");
  assert.equal(result.offers[0].priceLabel, "NZ$ 600");
});

test("STAah zero inventory is definitely unavailable", () => {
  const result = parseStaah(
    { PropertyList: [{ DayRate: { "2027-02-05": { Inventory: 0, Rate: 0 } } }] },
    hotel,
  );
  assert.equal(result.status, "unavailable");
});

test("Ibex only exposes rooms with positive inventory", () => {
  const result = parseIbex(
    [
      {
        id: 1,
        info: { "marketing-name": "Lake View Suite" },
        availability: [1],
        "sell-mode": [{ mode: 2 }],
        rates: [{ id: 10, enabled: true, "available-for-online-bookings": true, "valid-for": { adults: true } }],
        prices: { 10: [{ "min-stays": { 1: { "base-price": 420, "adults-prices": { 2: 420 } } } }] },
      },
      { id: 2, info: { "marketing-name": "Sold out" }, availability: [0], rates: [], prices: {} },
    ],
    hotel,
  );
  assert.equal(result.status, "available");
  assert.equal(result.offers.length, 1);
  assert.equal(result.offers[0].roomName, "Lake View Suite");
  assert.equal(result.offers[0].priceLabel, "NZ$ 420");
});

test("Accor explicit unavailable status is definitive", () => {
  const result = parseAccor(
    { data: { hotelOffers: { availability: { status: "UNAVAILABLE" }, offersSelection: { offers: [] } } } },
    hotel,
  );
  assert.equal(result.status, "unavailable");
});

test("Accor offers produce a validation candidate", () => {
  const result = parseAccor(
    {
      data: {
        hotelOffers: {
          availability: { status: "AVAILABLE" },
          offersSelection: {
            offers: [
              {
                id: "offer-1",
                description: "Deluxe Lake View Room",
                product: { id: "room-1" },
                rate: { label: "Flexible" },
                pricing: { main: { formattedAmount: "NZ$ 500" } },
              },
            ],
          },
        },
      },
    },
    hotel,
  );
  assert.equal(result.status, "available");
  assert.equal(result.confidence, "candidate");
  assert.equal(result.offers[0].roomName, "Deluxe Lake View Room");
});

test("Agilysys empty roomTypes means unavailable", () => {
  const result = parseAgilysys(
    { Success: false, offers: [{ offerCode: "DYNRO", offerAvailable: true, roomTypes: [] }] },
    hotel,
  );
  assert.equal(result.status, "unavailable");
});

test("Newbook criteria failure means unavailable", () => {
  const html = `<div class="newbook_online_category_box">${"x".repeat(120)}It appears the accommodation options and dates you have selected do not meet the required criteria for a booking</div>`;
  const result = parseNewbook(html, hotel);
  assert.equal(result.status, "unavailable");
});

test("Newbook extracts a bookable room", () => {
  const html = `<div class="newbook_online_category_box">
    <div class="newbook_online_category_row_category_name"><a>Beach Cabin</a></div>
    <span class="newbook_online_from_price_text">NZ$ 350</span>
    <button>Book now</button>${"x".repeat(120)}
  </div>`;
  const result = parseNewbook(html, hotel);
  assert.equal(result.status, "available");
  assert.equal(result.offers[0].roomName, "Beach Cabin");
  assert.equal(result.offers[0].priceLabel, "NZ$ 350");
});
