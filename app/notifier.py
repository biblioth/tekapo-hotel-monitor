from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from datetime import date
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

HOTEL_SHORT_NAMES = {
    "Ranginui at Lake Tekapo": "Ranginui",
    "Lakeview Tekapo": "Lakeview",
    "Grand Suites Lake Tekapo": "Grand Suites",
    "Galaxy Boutique Hotel": "Galaxy Boutique",
    "Peppers Bluewater Resort Lake Tekapo": "Peppers Bluewater",
    "The Hermitage Hotel Mt Cook": "Hermitage Mt Cook",
    "Tasman Holiday Parks Hahei Beach": "Hahei Beach",
}


def build_pushplus_title(event: dict[str, Any]) -> str:
    """Keep the useful part visible in WeChat's collapsed notification card."""
    payload = event["payload"]
    offers = payload["offers"]
    offer = offers[0]
    hotel = HOTEL_SHORT_NAMES.get(payload["hotel_name"], payload["hotel_name"])
    if len(offers) > 1:
        detail = f"新增 {len(offers)} 个房型"
    else:
        detail = str(offer["room_name"])
        if len(detail) > 24:
            detail = detail[:23] + "…"
    price = offer.get("price_label")
    parts = [f"🔔 {hotel}", detail]
    if price:
        parts.append(str(price))
    return "｜".join(parts)[:80]


def build_pushplus_text_title(message: str) -> str:
    """Surface short text notifications, such as the daily summary, without a tap."""
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return "LakeWatch"
    return "｜".join(lines[:2])[:80]


def _short_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return f"{parsed.year}/{parsed.month}/{parsed.day}"


def _cancellation_text(offer: dict[str, Any]) -> str | None:
    if not offer.get("free_cancellation"):
        return None
    until_date = offer.get("free_cancellation_until_date")
    until_time = offer.get("free_cancellation_until_time")
    if until_date:
        until = _short_date(str(until_date))
        if until_time:
            until += f" {until_time}"
        return f"免费取消至 {until}"
    return "可免费取消"


def render_alert(settings: Settings, event: dict[str, Any]) -> str:
    payload = event["payload"]
    offers = payload["offers"]
    hotel = HOTEL_SHORT_NAMES.get(payload["hotel_name"], payload["hotel_name"])
    if event["event_type"] == "availability_returned":
        headline = "重新有房" if len(offers) == 1 else f"重新有房（{len(offers)} 个房型）"
    else:
        headline = "新增房型" if len(offers) == 1 else f"新增 {len(offers)} 个房型"
    check_in = payload.get("check_in") or settings.check_in.isoformat()
    check_out = payload.get("check_out") or settings.check_out.isoformat()
    stay = f"{_short_date(str(check_in))}–{_short_date(str(check_out))}"
    first = offers[0]
    lines = [f"🔔 {hotel} {headline}"]
    if len(offers) == 1:
        lines.append(f"{stay} · {first['room_name']}")
        details = [value for value in (first.get("price_label"), _cancellation_text(first)) if value]
        if details:
            lines.append(" · ".join(str(value) for value in details))
    else:
        price = first.get("price_label")
        lines.append(f"{stay}{f' · 最低 {price}' if price else ''}")
        room_names = "、".join(str(offer["room_name"]) for offer in offers[:3])
        if len(offers) > 3:
            room_names += f"等 {len(offers)} 个房型"
        lines.append(room_names)
    link = first.get("link")
    if link:
        lines.append(f"立即预订：{link}")
    return "\n".join(lines)


class FeishuNotifier:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def send(self, event: dict[str, Any]) -> None:
        message = render_alert(self.settings, event)
        await self.send_text(message)

    async def send_text(self, message: str) -> None:
        if not self.settings.feishu_webhook_url:
            logger.info("Feishu webhook not configured; alert logged only: %s", message)
            return
        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": message},
        }
        if self.settings.feishu_webhook_secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{self.settings.feishu_webhook_secret}"
            digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
            payload["timestamp"] = timestamp
            payload["sign"] = base64.b64encode(digest).decode("utf-8")

        response = await self.client.post(self.settings.feishu_webhook_url, json=payload)
        response.raise_for_status()
        data = response.json()
        code = data.get("code", data.get("StatusCode", 0))
        if code not in (0, "0", None):
            raise RuntimeError(f"Feishu webhook rejected the message: {data}")


class PushPlusNotifier:
    endpoint = "https://www.pushplus.plus/send"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

    @property
    def configured(self) -> bool:
        return bool(self.settings.pushplus_token)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def send(self, event: dict[str, Any]) -> None:
        await self.send_text(render_alert(self.settings, event), title=build_pushplus_title(event))

    async def send_text(self, message: str, title: str | None = None) -> None:
        if not self.settings.pushplus_token:
            logger.info("PushPlus token not configured; channel skipped")
            return
        failures = []
        for channel in dict.fromkeys(getattr(self.settings, "pushplus_channels", ("wechat",))):
            payload = {
                "token": self.settings.pushplus_token,
                "title": title or build_pushplus_text_title(message),
                "content": message,
                "template": "txt",
                "channel": channel,
            }
            if self.settings.pushplus_topic:
                payload["topic"] = self.settings.pushplus_topic

            try:
                response = await self.client.post(self.endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                if data.get("code") not in (200, "200"):
                    raise RuntimeError(f"PushPlus rejected the message: {data}")
            except Exception as error:
                failures.append(f"{channel}: {error}")
        if failures:
            raise RuntimeError("PushPlus channel delivery failed: " + "; ".join(failures))


class FanoutNotifier:
    """Deliver to every configured channel without letting one hide another."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.channels = [
            ("feishu", FeishuNotifier(settings), bool(settings.feishu_webhook_url)),
            ("pushplus", PushPlusNotifier(settings), bool(settings.pushplus_token)),
        ]

    async def close(self) -> None:
        await asyncio.gather(*(channel.close() for _, channel, _ in self.channels))

    async def send(self, event: dict[str, Any]) -> None:
        await self._deliver("send", event)

    async def send_text(self, message: str) -> None:
        await self._deliver("send_text", message)

    async def _deliver(self, method: str, value: Any) -> None:
        configured = [(name, channel) for name, channel, enabled in self.channels if enabled]
        if not configured:
            logger.info("No notification channel configured; message logged only: %s", value)
            return

        results = await asyncio.gather(
            *(getattr(channel, method)(value) for _, channel in configured),
            return_exceptions=True,
        )
        failures = [
            f"{name}: {result}"
            for (name, _), result in zip(configured, results, strict=True)
            if isinstance(result, BaseException)
        ]
        for (name, _), result in zip(configured, results, strict=True):
            if not isinstance(result, BaseException):
                logger.info("Notification delivered: channel=%s", name)
        for failure in failures:
            logger.error("Notification channel failed: %s", failure)
        if len(failures) == len(configured):
            raise RuntimeError("All notification channels failed: " + "; ".join(failures))
