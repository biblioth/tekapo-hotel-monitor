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
