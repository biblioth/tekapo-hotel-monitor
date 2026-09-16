from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx

from app.config import Settings
from app.models import Hotel, HotelResult, Offer
from app.provider import DirectWebsiteProvider


def _offer_payload(offer: Offer) -> dict[str, Any]:
    return {
        "identity": offer.identity,
        "roomName": offer.room_name,
        "link": offer.link,
        "priceLabel": offer.price_label,
        "priceValue": offer.price_value,
        "totalPriceLabel": offer.total_price_label,
        "freeCancellation": offer.free_cancellation,
        "freeCancellationUntilDate": offer.free_cancellation_until_date,
        "freeCancellationUntilTime": offer.free_cancellation_until_time,
        "official": offer.official,
        "breakfastIncluded": offer.breakfast_included,
        "inclusions": list(offer.inclusions),
    }


def _callback_payload(
    hotel: Hotel, result: HotelResult, duration_ms: int
) -> dict[str, Any]:
    return {
        "event_id": os.getenv("LAKEWATCH_EVENT_ID") or None,
        "idempotency_key": os.getenv("LAKEWATCH_IDEMPOTENCY_KEY") or None,
        "cycle_id": os.getenv("LAKEWATCH_CYCLE_ID") or None,
        "hotel_key": hotel.key,
        "reason": os.getenv("LAKEWATCH_VALIDATION_REASON") or "browser-validation",
        "duration_ms": duration_ms,
        "observation": {
            "status": result.status,
            "offers": [_offer_payload(offer) for offer in result.offers],
            "message": result.message,
        },
    }


async def run_browser_validation() -> dict[str, Any]:
    settings = Settings.from_env()
    hotel_key = os.environ["LAKEWATCH_HOTEL_KEY"].strip()
    callback_url = os.environ["CLOUDFLARE_VALIDATION_URL"].strip()
    callback_token = os.environ["CLOUDFLARE_VALIDATION_TOKEN"].strip()
    hotel = next((item for item in settings.load_hotels() if item.key == hotel_key), None)
    if hotel is None:
        raise ValueError(f"Unknown LAKEWATCH_HOTEL_KEY: {hotel_key}")

    provider = DirectWebsiteProvider(settings)
    started = time.monotonic()
    try:
        result = await provider.check(hotel)
    finally:
        await provider.close()
    duration_ms = round((time.monotonic() - started) * 1000)
    payload = _callback_payload(hotel, result, duration_ms)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            callback_url,
            headers={"authorization": f"Bearer {callback_token}"},
            json=payload,
        )
        response.raise_for_status()
        callback = response.json()
    output = {"payload": payload, "callback": callback}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    if result.status == "error":
        raise RuntimeError(result.message or f"Browser validation failed for {hotel.key}")
    return output


def main() -> None:
    asyncio.run(run_browser_validation())


if __name__ == "__main__":
    main()
