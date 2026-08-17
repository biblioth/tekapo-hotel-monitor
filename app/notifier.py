from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class FeishuNotifier:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def send(self, event: dict[str, Any]) -> None:
        message = self.render(event)
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

    def render(self, event: dict[str, Any]) -> str:
        payload = event["payload"]
        offers = payload["offers"]
        headline = "重新放房" if event["event_type"] == "availability_returned" else "出现新房型"
        lines = [
            "🔔 Lake Tekapo 捡漏",
            f"{payload['hotel_name']}：{headline}",
            f"入住：{self.settings.check_in.isoformat()} → {self.settings.check_out.isoformat()}",
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
