import { available, expectJson, fetchWithTimeout, unavailable } from "../http.js";

export function parseStaah(data, hotel) {
  const properties = data?.PropertyList;
  if (!Array.isArray(properties) || properties.length === 0) {
    throw new Error("STAah response is missing PropertyList");
  }
  const day = properties[0]?.DayRate?.[hotel.checkIn];
  if (!day || typeof day.Inventory !== "number") {
    throw new Error("STAah response is missing the requested date inventory");
  }
  if (day.Inventory <= 0) {
    return unavailable("STAah reports zero property inventory");
  }
  return available(
    [
      {
        identity: `property-${hotel.propertyId}`,
        roomName: "Ranginui（具体房型待官网确认）",
        priceLabel: day.Rate > 0 ? `NZ$ ${day.Rate}` : null,
        link: hotel.bookingUrl,
        official: true,
      },
    ],
    `STAah reports ${day.Inventory} unit(s) at property level`,
    "candidate",
  );
}

export async function checkStaah(hotel, fetcher = fetch) {
  const url = new URL("https://csbe.staah.net/");
  url.search = new URLSearchParams({
    RequestType: "berate",
    PropertyId: hotel.propertyId,
    Product: "no",
    FromDate: hotel.checkIn,
    ToDate: hotel.checkOut,
    JDRN: "Y",
    Country: "SG",
    DeviceType: "desktop",
    Lang: "EN",
  });
  const response = await fetchWithTimeout(fetcher, url, {
    headers: { "x-api-key": hotel.apiKey, accept: "application/json" },
  });
  return parseStaah(await expectJson(response, "STAah"), hotel);
}
