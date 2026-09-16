"""Record booking-engine XHR/fetch traffic while running a normal hotel check.

This is a developer tool for building and repairing lightweight sensor adapters.
It never prints request headers or cookies, because those may contain credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from playwright.async_api import Request, Response, async_playwright

from app.config import Settings
from app.provider import DirectWebsiteProvider


_INVENTORY_HINTS = (
    "availability",
    "RequestType=berate",
    "/graphql",
    "rate-service/",
    "/services/query",
    "generate-token",
    "generatetoken/",
)


async def discover(hotel_key: str, output: Path) -> None:
    settings = Settings.from_env()
    hotel = next((item for item in settings.load_hotels() if item.key == hotel_key), None)
    if hotel is None:
        raise SystemExit(f"Unknown hotel key: {hotel_key}")

    traffic: list[dict[str, Any]] = []
    pending: dict[int, dict[str, Any]] = {}
    response_tasks: set[asyncio.Task[None]] = set()

    def safe_post_data(request: Request) -> str | None:
        try:
            return request.post_data
        except UnicodeDecodeError:
            return "<binary>"

    def on_request(request: Request) -> None:
        if request.resource_type not in {"xhr", "fetch"}:
            return
        record = {
            "method": request.method,
            "resource_type": request.resource_type,
            "url": request.url,
            "post_data": safe_post_data(request),
            "status": None,
            "content_type": None,
            "response_body": None,
            "api_headers": {
                key: value
                for key, value in request.headers.items()
                if key.casefold().startswith(("x-api", "api-key", "apikey", "app-id"))
            },
            "header_names": sorted(request.headers),
        }
        traffic.append(record)
        pending[id(request)] = record

    async def on_response(response: Response) -> None:
        record = pending.get(id(response.request))
        if record is None:
            return
        record["status"] = response.status
        record["content_type"] = response.headers.get("content-type")
        record["response_header_names"] = sorted(response.headers)
        if any(hint.casefold() in response.url.casefold() for hint in _INVENTORY_HINTS):
            try:
                record["response_body"] = (await response.text())[:1_000_000]
            except Exception as exc:
                record["response_body"] = f"<unreadable: {exc}>"

    def schedule_response(response: Response) -> None:
        task = asyncio.create_task(on_response(response))
        response_tasks.add(task)
        task.add_done_callback(response_tasks.discard)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            executable_path=settings.chromium_executable_path,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="en-NZ",
            timezone_id="Pacific/Auckland",
            viewport={"width": 1440, "height": 1000},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()
        page.set_default_timeout(settings.browser_timeout_seconds * 1000)
        page.on("request", on_request)
        page.on("response", schedule_response)

        provider = DirectWebsiteProvider(settings)
        try:
            result = await provider._dispatch(page, hotel)
            result_summary = {
                "status": result.status,
                "offers": len(result.offers),
                "message": result.message,
            }
        except Exception as exc:
            result_summary = {"status": "error", "message": str(exc)}
        finally:
            if response_tasks:
                await asyncio.gather(*tuple(response_tasks), return_exceptions=True)
            await context.close()
            await browser.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {"hotel": hotel.key, "result": result_summary, "traffic": traffic},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"hotel": hotel.key, "result": result_summary, "requests": len(traffic)}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("hotel_key")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(discover(args.hotel_key, args.output))


if __name__ == "__main__":
    main()
