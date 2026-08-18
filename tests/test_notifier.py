from datetime import date
from types import SimpleNamespace

import pytest

from app.notifier import FanoutNotifier, PushPlusNotifier


class FakeResponse:
    def __init__(self, data: dict):
        self.data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.data


class FakeClient:
    def __init__(self, data: dict):
        self.data = data
        self.requests: list[tuple[str, dict]] = []

    async def post(self, url: str, json: dict) -> FakeResponse:
        self.requests.append((url, json))
        return FakeResponse(self.data)


class StubChannel:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.messages: list[str] = []

    async def send_text(self, message: str) -> None:
        if self.error:
            raise self.error
        self.messages.append(message)

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_pushplus_uses_topic_and_wechat_channel() -> None:
    settings = SimpleNamespace(pushplus_token="test-message-token", pushplus_topic="lakewatch20270205")
    client = FakeClient({"code": 200, "msg": "请求成功"})
    notifier = PushPlusNotifier(settings, client=client)

    await notifier.send_text("有新房", title="LakeWatch 测试")

    assert client.requests == [
        (
            "https://www.pushplus.plus/send",
            {
                "token": "test-message-token",
                "title": "LakeWatch 测试",
                "content": "有新房",
                "template": "txt",
                "channel": "wechat",
                "topic": "lakewatch20270205",
            },
        )
    ]


@pytest.mark.asyncio
async def test_pushplus_event_title_contains_actionable_summary() -> None:
    settings = SimpleNamespace(
        pushplus_token="test-message-token",
        pushplus_topic="lakewatch20270205",
        check_in=date(2027, 2, 5),
        check_out=date(2027, 2, 6),
    )
    client = FakeClient({"code": 200, "msg": "请求成功"})
    notifier = PushPlusNotifier(settings, client=client)
    event = {
        "event_type": "availability_returned",
        "payload": {
            "hotel_name": "Peppers Bluewater Resort Lake Tekapo",
            "offers": [
                {
                    "room_name": "Deluxe Lake View Room",
                    "price_label": "NZ$420",
                    "free_cancellation": True,
                    "source": "Official",
                    "official": True,
                    "link": "https://example.com/book",
                }
            ],
        },
    }

    await notifier.send(event)

    assert client.requests[0][1]["title"] == (
        "🔔 Peppers Bluewater｜Deluxe Lake View Room｜NZ$420｜可免费取消｜立即订"
    )


@pytest.mark.asyncio
async def test_pushplus_daily_summary_is_visible_in_title() -> None:
    settings = SimpleNamespace(pushplus_token="test-message-token", pushplus_topic="lakewatch20270205")
    client = FakeClient({"code": 200, "msg": "请求成功"})
    notifier = PushPlusNotifier(settings, client=client)
    message = (
        "📊 酒店监控日报｜2026-08-17\n"
        "执行 24 次｜成功 24｜异常 0\n"
        "放房变化 0｜已提醒 0\n"
        "当前：有房 1 家｜无房 5 家"
    )

    await notifier.send_text(message)

    assert client.requests[0][1]["title"] == (
        "📊 酒店监控日报｜2026-08-17｜执行 24 次｜成功 24｜异常 0｜放房变化 0｜已提醒 0"
    )


@pytest.mark.asyncio
async def test_fanout_keeps_working_when_one_channel_fails() -> None:
    notifier = FanoutNotifier(SimpleNamespace(feishu_webhook_url=None, pushplus_token=None))
    working = StubChannel()
    failing = StubChannel(RuntimeError("temporary failure"))
    notifier.channels = [("working", working, True), ("failing", failing, True)]

    await notifier.send_text("hello")

    assert working.messages == ["hello"]


@pytest.mark.asyncio
async def test_fanout_retries_when_every_channel_fails() -> None:
    notifier = FanoutNotifier(SimpleNamespace(feishu_webhook_url=None, pushplus_token=None))
    notifier.channels = [
        ("one", StubChannel(RuntimeError("one failed")), True),
        ("two", StubChannel(RuntimeError("two failed")), True),
    ]

    with pytest.raises(RuntimeError, match="All notification channels failed"):
        await notifier.send_text("hello")
