import { available, expectJson, fetchWithTimeout, unavailable } from "../http.js";

export function parseIbex(rooms, hotel) {
  if (!Array.isArray(rooms)) {
    throw new Error("Ibex response is not a room array");
  }
  const offers = rooms
    .filter((room) => room?.info?.["marketing-name"] !== "dummy")
    .filter((room) => Array.isArray(room.availability) && room.availability.every((value) => value > 0))
    .filter((room) => !Array.isArray(room["sell-mode"]) || room["sell-mode"].every((item) => item.mode !== 0))
    .map((room) => {
      const rates = Array.isArray(room.rates)
        ? room.rates.filter((rate) => rate.enabled && rate["available-for-online-bookings"])
        : [];
      const nightly = rates.find((rate) => rate?.["valid-for"]?.adults) || rates[0];
      const datePrice = nightly ? room.prices?.[nightly.id]?.[0] : null;
      const stay = datePrice?.["min-stays"]?.["1"];
      const price = stay?.["adults-prices"]?.[String(hotel.adults)] ?? stay?.["base-price"];
      return {
        identity: String(room.id),
        roomName: room.info["marketing-name"],
        priceLabel: typeof price === "number" ? `NZ$ ${price}` : null,
        link: hotel.bookingUrl,
        official: true,
      };
    });
  return offers.length > 0
    ? available(offers)
    : unavailable("Ibex reports no rooms with positive inventory");
}

export async function checkIbex(hotel, fetcher = fetch) {
  const tokenResponse = await fetchWithTimeout(fetcher, "https://www.ibexres.com/fbs-auth/generate-token", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({
      key: hotel.clientKey,
      referrer: hotel.referrer,
      client: hotel.client,
      venue: false,
    }),
  });
  if (!tokenResponse.ok) {
    throw new Error(`Ibex token endpoint returned HTTP ${tokenResponse.status}`);
  }
  const authorization = tokenResponse.headers.get("authorization");
  if (!authorization) {
    throw new Error("Ibex token response is missing authorization header");
  }

  const url = new URL(
    `https://api.ibexres.com/fbs/properties/${hotel.propertyId}/rooms/all/availability`,
  );
  url.search = new URLSearchParams({
    package_price: "true",
    from: hotel.checkIn,
    to: hotel.checkIn,
    dailyTo: hotel.checkOut,
    rates: "",
  });
  const response = await fetchWithTimeout(fetcher, url, {
    headers: {
      authorization,
      key: hotel.clientKey,
      accept: "application/json",
      referer: hotel.bookingUrl,
    },
  });
  return parseIbex(await expectJson(response, "Ibex availability"), hotel);
}
