from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any
from urllib.parse import urlencode

from app.config import Settings
from app.models import Hotel, HotelResult, Offer

logger = logging.getLogger(__name__)


class DirectWebsiteProvider:
    """Read the hotels' public booking engines without a paid search API."""

    _unavailable_terms = (
        "no rooms available",
        "no room available",
        "no rooms found",
        "no room found",
        "sold out",
        "not available",
        "this accommodation is unavailable",
        "nothing to book right now",
        "no accommodation available",
        "couldn't find any available",
        "could not find any available",
        "property is not bookable during these dates",
    )
    _action_re = re.compile(r"\b(book|select|reserve|choose|add room|view rates?)\b", re.I)
    _price_re = re.compile(
        r"(?P<label>(?:NZD|NZ\$|AUD|EUR|€|\$)\s*[0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        re.I,
    )

    def __init__(self, settings: Settings):
        self.settings = settings
        self._playwright: Any = None
        self._browser: Any = None
        self._startup_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        async with self._startup_lock:
            if self._browser is not None:
                return self._browser
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            launch_options: dict[str, Any] = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled"],
            }
            if self.settings.chromium_executable_path:
                launch_options["executable_path"] = self.settings.chromium_executable_path
            self._browser = await self._playwright.chromium.launch(**launch_options)
            return self._browser

    async def check(self, hotel: Hotel) -> HotelResult:
        last_error: Exception | None = None
        for attempt in range(self.settings.browser_retries):
            context = None
            try:
                browser = await self._ensure_browser()
                context = await browser.new_context(
                    locale="en-NZ",
                    timezone_id="Pacific/Auckland",
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1440, "height": 1000},
                )
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                await context.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in {"image", "media", "font"}
                    else route.continue_(),
                )
                page = await context.new_page()
                page.set_default_timeout(self.settings.browser_timeout_seconds * 1000)
                result = await self._dispatch(page, hotel)
                return result
            except Exception as exc:  # Booking sites change often; preserve the last good snapshot.
                last_error = exc
                logger.warning(
                    "Direct website query failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.settings.browser_retries,
                    exc,
                    extra={"hotel_key": hotel.key},
                )
                if attempt + 1 < self.settings.browser_retries:
                    await asyncio.sleep(2**attempt)
            finally:
                if context is not None:
                    await context.close()
        return HotelResult(
            hotel=hotel,
            status="error",
            property_name=hotel.name,
            message=f"Official booking site query failed: {last_error}",
            raw_summary={"engine": hotel.engine, "url": hotel.booking_url},
        )

    async def _dispatch(self, page: Any, hotel: Hotel) -> HotelResult:
        handlers = {
            "preno": self._check_preno,
            "lakeview_wix": self._check_lakeview,
            "ibex": self._check_ibex,
            "siteminder": self._check_siteminder,
            "accor": self._check_accor,
            "agilysys": self._check_agilysys,
        }
        try:
            handler = handlers[hotel.engine]
        except KeyError as exc:
            raise RuntimeError(f"Unsupported booking engine: {hotel.engine}") from exc
        return await handler(page, hotel)

    async def _goto(self, page: Any, url: str) -> None:
        await page.goto(url, wait_until="domcontentloaded")
        await page.locator("body").wait_for(state="visible")

    async def _check_preno(self, page: Any, hotel: Hotel) -> HotelResult:
        await self._goto(page, hotel.booking_url)
        await page.locator("#CheckIn-Date").wait_for()
        await page.wait_for_timeout(3500)
        await self._choose_preno_date(page, "#CheckIn-Date", self.settings.check_in)
        await self._choose_preno_date(
            page, "#CheckOut-Date", self.settings.check_out, reuse_open_picker=True
        )
        await page.locator("#searchbutton").click()
        await page.wait_for_timeout(3500)
        return await self._result_from_page(page, hotel, "Ranginui At Lake Tekapo")

    async def _choose_preno_date(
        self, page: Any, selector: str, target: date, reuse_open_picker: bool = False
    ) -> None:
        if not reuse_open_picker or await page.locator(".rdrMonthName:visible").count() == 0:
            await page.locator(selector).click()
            await page.locator(".rdrMonthName:visible").first.wait_for()
        month_label = target.strftime("%b %Y")
        for _ in range(24):
            labels = await page.locator(".rdrMonth:visible .rdrMonthName").all_inner_texts()
            if any(month_label.casefold() in label.casefold() for label in labels):
                break
            await page.locator(".rdrNextButton:visible").click(force=True)
            await page.wait_for_timeout(250)
        else:
            raise RuntimeError(f"Preno calendar could not reach {month_label}")
        month = page.locator(".rdrMonth:visible").filter(
            has_text=re.compile(re.escape(month_label), re.I)
        )
        day = month.locator("button.rdrDay:not(.rdrDayPassive):not([disabled])").filter(
            has_text=re.compile(rf"^\s*{target.day}\s*$")
        )
        await day.first.click(force=True)
        await page.wait_for_timeout(250)

    async def _check_lakeview(self, page: Any, hotel: Hotel) -> HotelResult:
        await self._goto(page, hotel.booking_url)
        await page.wait_for_timeout(2500)
        text = await page.locator("body").inner_text()
        folded = text.casefold()
        if "lakeview tekapo" not in folded:
            raise RuntimeError("Lakeview official page did not load its expected content")
        if "nothing to book right now" in folded:
            return self._unavailable(hotel, "Official page says there is nothing to book")
        if re.search(r"\b(book|reserve|availability)\b", text, re.I):
            offer = Offer(
                source=hotel.name,
                room_name=hotel.room_names[0],
                link=hotel.booking_url,
                official=True,
            )
            return HotelResult(
                hotel=hotel,
                status="available",
                offers=(offer,),
                property_name=hotel.name,
                raw_summary={"engine": hotel.engine, "generic_listing": True},
            )
        raise RuntimeError("Lakeview page loaded, but its booking state was not recognisable")

    async def _check_ibex(self, page: Any, hotel: Hotel) -> HotelResult:
        await self._goto(page, hotel.booking_url)
        check_in = page.locator('input[placeholder="Check In Date"]')
        check_out = page.locator('input[placeholder="Check Out Date"]')
        await check_in.wait_for()
        await self._fill_date(check_in, self.settings.check_in.strftime("%d/%m/%Y"))
        await self._fill_date(check_out, self.settings.check_out.strftime("%d/%m/%Y"))
        await page.get_by_role("button", name="Search", exact=True).click()
        await page.wait_for_timeout(4500)
        return await self._result_from_page(page, hotel, "Grand Suites")

    @staticmethod
    async def _fill_date(locator: Any, value: str) -> None:
        await locator.fill(value)
        await locator.evaluate(
            "(el) => { el.dispatchEvent(new Event('input', {bubbles:true})); "
            "el.dispatchEvent(new Event('change', {bubbles:true})); el.blur(); }"
        )

    async def _check_siteminder(self, page: Any, hotel: Hotel) -> HotelResult:
        query = urlencode(
            {
                "locale": "en",
                "checkInDate": self.settings.check_in.isoformat(),
                "checkOutDate": self.settings.check_out.isoformat(),
                "items[0][adults]": self.settings.adults,
                "items[0][children]": 0,
                "items[0][infants]": 0,
                "currency": self.settings.currency,
            }
        )
        url = f"{hotel.booking_url}?{query}"
        await self._goto(page, url)
        await page.wait_for_timeout(6500)
        text = await page.locator("body").inner_text()
        # SiteMinder sometimes completes its public anti-bot challenge only after first load.
        if not any(name.casefold() in text.casefold() for name in hotel.room_names):
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(4500)
        return await self._result_from_page(page, hotel, "Galaxy")

    async def _check_accor(self, page: Any, hotel: Hotel) -> HotelResult:
        nights = (self.settings.check_out - self.settings.check_in).days
        query = urlencode(
            {
                "dateIn": self.settings.check_in.isoformat(),
                "nights": nights,
                "compositions": self.settings.adults,
                "stayplus": "false",
                "snu": "false",
                "hideHotelDetails": "true",
            }
        )
        await self._goto(page, f"{hotel.booking_url}?{query}")
        await page.wait_for_timeout(10000)
        return await self._result_from_page(page, hotel, "Peppers Bluewater Resort")

    async def _check_agilysys(self, page: Any, hotel: Hotel) -> HotelResult:
        await self._goto(page, hotel.booking_url)
        date_input = page.locator('input[placeholder="Click for Arrival-Departure date"]')
        await date_input.wait_for()
        await page.wait_for_timeout(3000)
        await page.locator('[automationid="offer-filter-datepicker"]').click(force=True)
        picker = page.locator(".md-drppicker").last
        await picker.locator(".calendar.left select.yearselect").select_option(
            label=str(self.settings.check_in.year), force=True
        )
        await picker.locator(".calendar.left select.monthselect").select_option(
            value=str(self.settings.check_in.month - 1), force=True
        )
        await self._click_agilysys_day(page, self.settings.check_in.day)
        await self._click_agilysys_day(page, self.settings.check_out.day)
        await page.wait_for_timeout(750)
        await page.locator('#offersCard0 button[automationid="offerSelectButton0"]').click()
        await page.wait_for_timeout(6000)
        return await self._result_from_page(page, hotel, "Hermitage Hotel")

    @staticmethod
    async def _click_agilysys_day(page: Any, day: int) -> None:
        calendar = page.locator(".md-drppicker").last.locator(".calendar.left")
        cell = calendar.locator("td.available:not(.off)").filter(
            has_text=re.compile(rf"^\s*{day}\s*$")
        )
        await cell.first.click(force=True)

    async def _result_from_page(
        self, page: Any, hotel: Hotel, expected_marker: str
    ) -> HotelResult:
        text = await page.locator("body").inner_text()
        offers = self.extract_offers(hotel, text, page.url)
        if offers:
            return HotelResult(
                hotel=hotel,
                status="available",
                offers=tuple(offers),
                property_name=hotel.name,
                raw_summary={"engine": hotel.engine, "url": page.url},
            )
        folded = text.casefold()
        if any(term in folded for term in self._unavailable_terms):
            return self._unavailable(hotel, "Official booking engine returned no rooms", page.url)
        if hotel.engine == "ibex" and re.search(r"\bUNAVAILABLE\b", text):
            return self._unavailable(hotel, "Official booking engine returned no rooms", page.url)
        if expected_marker.casefold() not in folded:
            raise RuntimeError(
                f"Expected marker '{expected_marker}' missing (blocked or booking site changed)"
            )
        excerpt = re.sub(r"\s+", " ", text).strip()[:500]
        raise RuntimeError(
            f"Booking page loaded, but no definitive availability state was found: {excerpt}"
        )

    def extract_offers(self, hotel: Hotel, text: str, link: str | None = None) -> list[Offer]:
        offers: list[Offer] = []
        folded = text.casefold()
        positions: list[tuple[int, str]] = []
        for room_name in hotel.room_names:
            pattern = re.compile(rf"(?<!\w){re.escape(room_name)}(?!\w)", re.I)
            positions.extend((match.start(), room_name) for match in pattern.finditer(text))
        positions.sort()

        for index, (start, room_name) in enumerate(positions):
            end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
            segment = text[start : min(end, start + 1800)]
            segment_folded = segment.casefold()
            if any(term in segment_folded for term in self._unavailable_terms) or re.search(
                r"\bUNAVAILABLE\b", segment
            ):
                continue
            price = self._price_re.search(segment)
            if price is None or self._action_re.search(segment) is None:
                continue
            label = re.sub(r"\s+", " ", price.group("label")).strip()
            numeric = re.sub(r"[^0-9.]", "", label.replace(",", ""))
            free_cancellation = bool(
                re.search(r"free cancellation|free cancellation|refundable", segment, re.I)
            )
            offers.append(
                Offer(
                    source=hotel.name,
                    room_name=room_name,
                    link=link or hotel.booking_url,
                    price_label=label,
                    price_value=float(numeric) if numeric else None,
                    total_price_label=label,
                    free_cancellation=free_cancellation,
                    official=True,
                    breakfast_included=bool(re.search(r"breakfast included", segment, re.I)),
                )
            )

        deduplicated: dict[str, Offer] = {}
        for offer in offers:
            previous = deduplicated.get(offer.identity)
            if previous is None or (offer.price_value or float("inf")) < (
                previous.price_value or float("inf")
            ):
                deduplicated[offer.identity] = offer
        return sorted(
            deduplicated.values(),
            key=lambda item: (item.price_value is None, item.price_value or float("inf"), item.room_name),
        )

    def _unavailable(self, hotel: Hotel, message: str, url: str | None = None) -> HotelResult:
        return HotelResult(
            hotel=hotel,
            status="unavailable",
            property_name=hotel.name,
            message=message,
            raw_summary={"engine": hotel.engine, "url": url or hotel.booking_url},
        )
