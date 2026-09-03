from datetime import date
from pathlib import Path

from app.config import Settings
from app.models import Hotel
from app.provider import DirectWebsiteProvider


def settings(tmp_path: Path) -> Settings:
    return Settings(
        feishu_webhook_url=None,
        feishu_webhook_secret=None,
        pushplus_token=None,
        pushplus_topic=None,
        check_in=date(2027, 2, 5),
        check_out=date(2027, 2, 6),
        adults=2,
        currency="NZD",
        timezone="Pacific/Auckland",
        cron_minute=0,
        run_on_startup=False,
        browser_timeout_seconds=30,
        browser_retries=1,
        chromium_executable_path=None,
        database_path=tmp_path / "db.sqlite",
        log_file=tmp_path / "log.jsonl",
        hotels_file=tmp_path / "hotels.json",
        log_retention_days=365,
        admin_token=None,
    )


def hotel() -> Hotel:
    return Hotel(
        "peppers",
        "Peppers Bluewater Resort Lake Tekapo",
        "accor",
        "https://example.com/book",
        ("Deluxe Lake View Hotel Room", "One Bedroom Suite"),
    )


def test_extracts_bookable_room_price_and_cancellation(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))
    text = """
    Deluxe Lake View Hotel Room
    Lake view · 2 guests
    Free cancellation until February 3
    NZD 499 total
    Select room
    """
    offers = provider.extract_offers(hotel(), text)

    assert len(offers) == 1
    assert offers[0].room_name == "Deluxe Lake View Hotel Room"
    assert offers[0].price_value == 499
    assert offers[0].free_cancellation is True
    assert offers[0].official is True


def test_unavailable_room_card_is_not_an_offer(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))
    text = "Deluxe Lake View Hotel Room\nUNAVAILABLE\nNZD 499\nSelect"
    assert provider.extract_offers(hotel(), text) == []


def test_static_catalog_without_price_or_action_is_not_inventory(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))
    text = "Deluxe Lake View Hotel Room\nA spacious room with a beautiful lake view."
    assert provider.extract_offers(hotel(), text) == []


def test_same_room_is_deduplicated_to_lowest_official_rate(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))
    text = """
    Deluxe Lake View Hotel Room\nNZD 520\nBook
    Deluxe Lake View Hotel Room\nNZD 480\nSelect
    """
    offers = provider.extract_offers(hotel(), text)
    assert len(offers) == 1
    assert offers[0].price_value == 480


def test_hotel_specific_stay_overrides_global_dates(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))
    hahei = Hotel(
        "hahei",
        "Tasman Holiday Parks Hahei Beach",
        "newbook",
        "https://example.com/book",
        check_in=date(2027, 2, 12),
        check_out=date(2027, 2, 13),
        adults=2,
    )

    assert provider._stay(hahei) == (date(2027, 2, 12), date(2027, 2, 13), 2)


def test_builds_newbook_offer_from_official_card(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))
    hahei = Hotel(
        "hahei",
        "Tasman Holiday Parks Hahei Beach",
        "newbook",
        "https://example.com/book",
    )

    offer = provider._newbook_offer(
        hahei,
        "Sea View Villas",
        "$423.00",
        "72 Hour Cancellation, Terms and conditions apply!",
        "https://example.com/book?dates",
    )

    assert offer.room_name == "Sea View Villas"
    assert offer.price_value == 423
    assert offer.free_cancellation is True
    assert offer.official is True


def test_newbook_loading_page_is_not_treated_as_unavailable(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))

    assert provider._newbook_page_state("Loading availability…") == "loading"


def test_newbook_minimum_stay_message_is_unavailable(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))

    assert provider._newbook_page_state("A minimum stay of 2 nights applies") == "unavailable"


def test_newbook_unknown_page_remains_retryable(tmp_path: Path) -> None:
    provider = DirectWebsiteProvider(settings(tmp_path))

    assert provider._newbook_page_state("Choose your accommodation") == "unknown"


def test_newbook_diagnostic_excerpt_is_compact() -> None:
    text = "Loading\n\n  availability   for your stay"

    assert DirectWebsiteProvider._compact_excerpt(text) == "Loading availability for your stay"


def test_browser_retry_uses_bounded_exponential_backoff() -> None:
    delays = [DirectWebsiteProvider._retry_delay_seconds(attempt) for attempt in range(4)]

    assert delays == [5.0, 10.0, 15.0, 15.0]


def test_newbook_retry_uses_longer_bounded_backoff() -> None:
    delays = [
        DirectWebsiteProvider._retry_delay_seconds(attempt, "newbook")
        for attempt in range(4)
    ]

    assert delays == [15.0, 30.0, 30.0, 30.0]


def test_newbook_network_error_reports_http_and_transport_failures() -> None:
    assert DirectWebsiteProvider._newbook_network_error({"http_status": 429}) == "HTTP 429"
    assert (
        DirectWebsiteProvider._newbook_network_error(
            {"http_status": None, "request_failure": "net::ERR_TIMED_OUT"}
        )
        == "net::ERR_TIMED_OUT"
    )
    assert DirectWebsiteProvider._newbook_network_error({"http_status": 200}) is None


def test_newbook_diagnostic_summary_keeps_missing_values_explicit() -> None:
    summary = DirectWebsiteProvider._newbook_diagnostic_summary(
        {
            "request_seen": True,
            "http_status": 200,
            "response_elapsed_ms": 1234,
            "request_failure": None,
            "page_error": None,
        }
    )

    assert "api_request=seen" in summary
    assert "api_status=200" in summary
    assert "api_response_ms=1234" in summary
    assert "request_failure=none" in summary
