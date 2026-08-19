import json
from dataclasses import replace
from datetime import date

from app.config import Settings


def test_loads_optional_hotel_specific_stay(tmp_path) -> None:
    hotels_file = tmp_path / "hotels.json"
    hotels_file.write_text(
        json.dumps(
            [
                {
                    "key": "hahei",
                    "name": "Tasman Holiday Parks Hahei Beach",
                    "engine": "newbook",
                    "booking_url": "https://example.com/book",
                    "check_in": "2027-02-12",
                    "check_out": "2027-02-13",
                    "adults": 2,
                }
            ]
        ),
        encoding="utf-8",
    )
    settings = replace(Settings.from_env(), hotels_file=hotels_file)

    hotel = settings.load_hotels()[0]

    assert hotel.check_in == date(2027, 2, 12)
    assert hotel.check_out == date(2027, 2, 13)
    assert hotel.adults == 2
