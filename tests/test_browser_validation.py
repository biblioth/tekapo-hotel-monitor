from app.browser_validation import _callback_payload, _offer_payload
from app.models import Hotel, HotelResult, Offer


def test_offer_payload_uses_worker_field_names() -> None:
    payload = _offer_payload(
        Offer(
            source="Hotel",
            room_name="Lake View Room",
            link="https://example.com/book",
            price_label="NZ$ 420",
            free_cancellation=True,
            official=True,
        )
    )

    assert payload["identity"] == "lakeviewroom"
    assert payload["roomName"] == "Lake View Room"
    assert payload["priceLabel"] == "NZ$ 420"
    assert payload["freeCancellation"] is True


def test_callback_payload_preserves_originating_cycle(monkeypatch) -> None:
    monkeypatch.setenv("LAKEWATCH_CYCLE_ID", "sensor-1790006400000")
    monkeypatch.setenv("LAKEWATCH_VALIDATION_REASON", "browser-only-watchdog")
    hotel = Hotel(
        key="lakeview-tekapo",
        name="Lakeview Tekapo",
        engine="lakeview_wix",
        booking_url="https://example.com",
    )
    result = HotelResult(hotel=hotel, status="unavailable")

    payload = _callback_payload(hotel, result, 321)

    assert payload["cycle_id"] == "sensor-1790006400000"
    assert payload["reason"] == "browser-only-watchdog"
    assert payload["duration_ms"] == 321
