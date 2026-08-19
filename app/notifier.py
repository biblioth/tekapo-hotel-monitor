from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
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
    """Put the decision-making details in the visible WeChat notification title."""
    payload = event["payload"]
    offer = payload["offers"][0]
    hotel = HOTEL_SHORT_NAMES.get(payload["hotel_name"], payload["hotel_name"])
    room = str(offer["room_name"])
    if len(room) > 22:
        room = room[:21] + "…"
    price = offer.get("price_label") or "价格待确认"
    cancellation = "可免费取消" if offer.get("free_cancellation") else "取消待确认"
    return f"🔔 {hotel}｜{room}｜{price}｜{cancellation}｜立即订"


def build_pushplus_text_title(message: str) -> str:
    """Surface short text notifications, such as the daily summary, without a tap."""
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return "LakeWatch"
    return "｜".join(lines[:3])[:80]


def render_alert(settings: Settings, event: dict[str, Any]) -> str:
    payload = event["payload"]
    offers = payload["offers"]
    headline = "重新放房" if event["event_type"] == "availability_returned" else "出现新房型"
    check_in = payload.get("check_in") or settings.check_in.isoformat()
    check_out = payload.get("check_out") or settings.check_out.isoformat()
    lines = [
        "🔔 LakeWatch 酒店捡漏",
        f"{payload['hotel_name']}：{headline}",
        f"入住：{check_in} → {check_out}",
    ]
    for offer in offers[:5]:
        cancellation = "不可免费取消/未披露"
        if offer.get("free_cancellation"):
            until = " ".join(
                value
                for value in (
                    offer.get("free_cancellation_until_date"),
                    offer.get("free_cancellation_until_time"),
                )
                if value
            )
            cancellation = f"免费取消{f'至 {until}' if until else ''}"
        stars = "⭐⭐⭐⭐⭐" if offer.get("free_cancellation") else ("⭐⭐⭐⭐" if offer.get("official") else "⭐⭐⭐")
        channel = f"{offer['source']}{'（官网）' if offer.get('official') else ''}"
        lines.extend(
            [
                "",
                f"房型：{offer['room_name']}",
                f"价格：{offer.get('price_label') or '未披露'} / 晚",
                f"取消：{cancellation}",
                f"渠道：{channel}",
                f"建议：{stars} 立即查看",
                f"预订：{offer.get('link') or '请打开渠道查询'}",
            ]
        )
    if len(offers) > 5:
        lines.append(f"\n另有 {len(offers) - 5} 个新房型，详见执行日志。")
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
        payload = {
            "token": self.settings.pushplus_token,
            "title": title or build_pushplus_text_title(message),
            "content": message,
            "template": "txt",
            "channel": "wechat",
        }
        if self.settings.pushplus_topic:
            payload["topic"] = self.settings.pushplus_topic

        response = await self.client.post(self.endpoint, json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get("code") not in (200, "200"):
            raise RuntimeError(f"PushPlus rejected the message: {data}")


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
