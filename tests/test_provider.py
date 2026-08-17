from datetime import date
from pathlib import Path

from app.config import Settings
from app.models import Hotel
from app.provider import DirectWebsiteProvider


def settings(tmp_path: Path) -> Settings:
    return Settings(
        feishu_webhook_url=None,
        feishu_webhook_secret=None,
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
