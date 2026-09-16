import { available, expectJson, fetchWithTimeout, unavailable } from "../http.js";

function firstValue(object, keys) {
  for (const key of keys) {
    const value = object?.[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return null;
}

export function parseAgilysys(data, hotel) {
  if (!Array.isArray(data?.offers)) {
    throw new Error("Agilysys response is missing offers");
  }
  const roomTypes = data.offers.flatMap((offer) =>
    Array.isArray(offer.roomTypes) ? offer.roomTypes : [],
  );
  const offers = roomTypes.map((room, index) => {
    const roomName = firstValue(room, [
      "roomTypeDescription",
      "roomTypeName",
      "description",
      "name",
      "roomType",
      "roomCode",
    ]);
    if (!roomName) {
      throw new Error("Agilysys room is missing a recognisable name");
    }
    const amount = firstValue(room, ["averageRate", "totalRate", "rate", "amount"]);
    return {
      identity: String(firstValue(room, ["roomTypeCode", "roomCode", "id"]) || `${roomName}-${index}`),
      roomName: String(roomName),
      priceLabel: typeof amount === "number" ? `NZ$ ${amount}` : null,
      link: hotel.bookingUrl,
      official: true,
    };
  });
  return offers.length > 0
    ? available(offers)
    : unavailable("Agilysys reports no bookable room types");
}

export async function checkAgilysys(hotel, fetcher = fetch) {
  const base = "https://book.hermitage.co.nz";
  const tokenResponse = await fetchWithTimeout(
    fetcher,
    `${base}/wbe-admin-service/generatetoken/v2/tenants/${hotel.tenantId}/propertyId/${hotel.propertyId}/appName/NA`,
    { headers: { accept: "application/json", referer: hotel.bookingUrl } },
  );
  const tokenData = await expectJson(tokenResponse, "Agilysys token");
  if (!tokenData.success || !tokenData.token) {
    throw new Error("Agilysys token response did not contain a token");
  }
  const path =
    `/wbe-rate-service/rate/tenants/${hotel.tenantId}/context/${hotel.contextId}` +
    `/propertyName/HT-GETRATES/offerPropertyName/HT-GETCODELIST/historyPropertyName/HT-GETHISTORY` +
    `/arrivalDate/${hotel.checkIn}/departureDate/${hotel.checkOut}` +
    `/casinoAccountNumber/NA/trips/1/adults/${hotel.adults}` +
    `/firstName/NA/lastName/NA`;
  const url = new URL(`${base}${path}`);
  url.search = new URLSearchParams({
    appName: "wbe",
    offer: hotel.offerCode,
    roomType: "NA",
    locale: "EN",
    includeDayGuestRoomType: "false",
  });
  const response = await fetchWithTimeout(fetcher, url, {
    headers: {
      authorization: `Bearer ${tokenData.token}`,
      accept: "application/json",
      referer: hotel.bookingUrl,
    },
  });
  return parseAgilysys(await expectJson(response, "Agilysys rates"), hotel);
}
