import { available, fetchWithTimeout, unavailable } from "../http.js";

const UNAVAILABLE_TERMS = [
  "do not meet the required criteria for a booking",
  "there are currently no sites available for online bookings",
  "there are currently no rates available for online bookings",
  "no accommodation available",
  "no available accommodation was found",
  "minimum stay",
  "minimum night",
];

function decodeHtml(value) {
  return value
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&nbsp;/g, " ")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

function dateLabel(value) {
  const date = new Date(`${value}T00:00:00Z`);
  return new Intl.DateTimeFormat("en-NZ", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function parseNewbook(html, hotel) {
  if (typeof html !== "string" || html.length < 100) {
    throw new Error("Newbook returned an empty or truncated response");
  }
  const cards = html
    .split(/(?=<[^>]+class=["'][^"']*newbook_online_category_box)/i)
    .filter((card) => /newbook_online_category_box/i.test(card));
  const offers = [];
  for (const card of cards) {
    if (!/\bBook now\b/i.test(card)) continue;
    const nameMatch = card.match(
      /newbook_online_category_row_category_name[\s\S]*?<a[^>]*>([\s\S]*?)<\/a>/i,
    );
    if (!nameMatch) continue;
    const priceMatch = card.match(
      /newbook_online_from_price_text[^>]*>([\s\S]*?)<\//i,
    );
    const roomName = decodeHtml(nameMatch[1]);
    offers.push({
      identity: roomName.toLocaleLowerCase("en"),
      roomName,
      priceLabel: priceMatch ? decodeHtml(priceMatch[1]) : null,
      link: hotel.bookingUrl,
      official: true,
      freeCancellation: /cancel/i.test(card),
    });
  }
  if (offers.length > 0) return available(offers);
  const folded = html.toLocaleLowerCase("en");
  if (UNAVAILABLE_TERMS.some((term) => folded.includes(term))) {
    return unavailable("Newbook reports no bookable one-night stay");
  }
  throw new Error("Newbook response contains neither bookable rooms nor an unavailable marker");
}

export async function checkNewbook(hotel, fetcher = fetch) {
  const nights = Math.round(
    (new Date(`${hotel.checkOut}T00:00:00Z`) - new Date(`${hotel.checkIn}T00:00:00Z`)) /
      86_400_000,
  );
  const body = new URLSearchParams({
    "force_category_type_id[]": "1",
    available_from: dateLabel(hotel.checkIn),
    available_to: dateLabel(hotel.checkOut),
    nights: String(nights),
    adults: String(hotel.adults),
    children: "0",
    infants: "0",
    language: "EN",
  });
  const response = await fetchWithTimeout(fetcher, hotel.endpoint, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
      accept: "text/html, */*",
      origin: "https://book.tasmanholidayparks.com",
      referer: hotel.bookingUrl,
    },
    body,
  }, 30_000);
  if (!response.ok) {
    throw new Error(`Newbook returned HTTP ${response.status}`);
  }
  return parseNewbook(await response.text(), hotel);
}
