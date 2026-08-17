from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


def normalize_identity(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[^a-z0-9]+", "", value)


@dataclass(frozen=True)
class Hotel:
    key: str
    name: str
    engine: str
    booking_url: str
    room_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Offer:
    source: str
    room_name: str
    link: str
    price_label: str | None = None
    price_value: float | None = None
    total_price_label: str | None = None
    free_cancellation: bool = False
    free_cancellation_until_date: str | None = None
    free_cancellation_until_time: str | None = None
    official: bool = False
    breakfast_included: bool = False
    inclusions: tuple[str, ...] = ()

    @property
    def identity(self) -> str:
        # A newly appearing OTA for an already-bookable room is not a new room release.
        return normalize_identity(self.room_name)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HotelResult:
    hotel: Hotel
    status: Literal["available", "unavailable", "error"]
    offers: tuple[Offer, ...] = ()
    property_name: str | None = None
    message: str | None = None
    raw_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def lowest_price(self) -> float | None:
        values = [offer.price_value for offer in self.offers if offer.price_value is not None]
        return min(values) if values else None
