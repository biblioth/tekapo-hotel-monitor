import { available, expectJson, fetchWithTimeout, unavailable } from "../http.js";

const QUERY = `query Sensor($hotelOffersHotelId: String!, $dateIn: Date!, $dateOut: Date!, $nbAdults: PositiveInt!, $countryMarket: String!, $currency: String!) {
  hotelOffers(hotelId: $hotelOffersHotelId, dateIn: $dateIn, dateOut: $dateOut, nbAdults: $nbAdults, childrenAges: [], countryMarket: $countryMarket, currency: $currency, use: NIGHT, hideMemberRate: false) {
    availability { status reasons { code label } }
    offersSelection(selectionStep: 0, totalRoomInBasket: 1) {
      offers {
        id description updatedRemaining
        product { id quantity updatedRemaining }
        rate { id label }
        pricing { currency main { amount formattedAmount simplifiedPolicies { cancellation { code label } } } }
      }
    }
  }
}`;

export function parseAccor(data, hotel) {
  if (Array.isArray(data?.errors) && data.errors.length > 0) {
    throw new Error(`Accor GraphQL error: ${data.errors[0].message}`);
  }
  const hotelOffers = data?.data?.hotelOffers;
  const status = hotelOffers?.availability?.status;
  const rawOffers = hotelOffers?.offersSelection?.offers;
  if (status === "UNAVAILABLE" && Array.isArray(rawOffers) && rawOffers.length === 0) {
    return unavailable("Accor reports the hotel unavailable for the selected dates");
  }
  if (!Array.isArray(rawOffers)) {
    throw new Error("Accor response is missing offersSelection.offers");
  }
  const offers = rawOffers.map((offer) => ({
    identity: String(offer.product?.id || offer.id),
    roomName: offer.description || offer.rate?.label || `Accor room ${offer.product?.id || offer.id}`,
    priceLabel: offer.pricing?.main?.formattedAmount || null,
    link: hotel.bookingUrl,
    official: true,
    freeCancellation: /free/i.test(
      offer.pricing?.main?.simplifiedPolicies?.cancellation?.label || "",
    ),
  }));
  if (status === "AVAILABLE" && offers.length > 0) {
    return available(offers, null, "candidate");
  }
  throw new Error(`Accor returned an inconsistent availability state: ${status}`);
}

export async function checkAccor(hotel, fetcher = fetch) {
  const response = await fetchWithTimeout(fetcher, "https://api.accor.com/bff/v1/graphql", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      apikey: hotel.apiKey,
      "app-id": "all.accor",
      lang: "en",
    },
    body: JSON.stringify({
      operationName: "Sensor",
      query: QUERY,
      variables: {
        hotelOffersHotelId: hotel.hotelId,
        dateIn: hotel.checkIn,
        dateOut: hotel.checkOut,
        nbAdults: hotel.adults,
        countryMarket: "NZ",
        currency: "NZD",
      },
    }),
  });
  return parseAccor(await expectJson(response, "Accor GraphQL"), hotel);
}
