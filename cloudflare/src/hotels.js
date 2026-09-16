import configuredHotels from "../../hotels.json" with { type: "json" };

function sensorHotel(hotel) {
  const sensor = hotel.sensor;
  return {
    key: hotel.key,
    name: hotel.name,
    engine: sensor.engine,
    checkIn: hotel.check_in,
    checkOut: hotel.check_out,
    adults: hotel.adults,
    bookingUrl: hotel.booking_url,
    propertyId: sensor.property_id,
    apiKey: sensor.api_key,
    client: sensor.client,
    clientKey: sensor.client_key,
    referrer: sensor.referrer,
    hotelId: sensor.hotel_id,
    tenantId: sensor.tenant_id,
    contextId: sensor.context_id,
    offerCode: sensor.offer_code,
    endpoint: sensor.endpoint,
  };
}

export const HOTELS = configuredHotels.filter((hotel) => hotel.sensor).map(sensorHotel);

export const BROWSER_ONLY_HOTELS = configuredHotels
  .filter((hotel) => !hotel.sensor)
  .map((hotel) => hotel.key);

export function hotelByKey(key) {
  const hotel = configuredHotels.find((item) => item.key === key);
  if (!hotel) return null;
  return {
    key: hotel.key,
    name: hotel.name,
    engine: hotel.sensor?.engine || hotel.engine,
    checkIn: hotel.check_in,
    checkOut: hotel.check_out,
    adults: hotel.adults,
    bookingUrl: hotel.booking_url,
  };
}
