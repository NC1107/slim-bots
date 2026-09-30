import json

from slimbots import gateway as gateway_module
from slimbots.gateway import Gateway


class FakeSocket:
    def __init__(self, hello):
        self._hello = hello
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        return json.dumps(self._hello)


class FakeClient:
    user_agent = "test"

    async def ws_ticket(self):
        return "ticket"

    def socket_url(self):
        return "ws://example.invalid/ws"


async def open_with(monkeypatch, hello):
    async def connect(url, **_kwargs):
        return FakeSocket(hello)

    monkeypatch.setattr(gateway_module.websockets, "connect", connect)
    return await Gateway.open(FakeClient())


async def test_open_keeps_the_hello_so_a_bot_can_read_the_moderation_head(monkeypatch):
    gateway = await open_with(monkeypatch, {"type": "hello", "protocol": 1, "moderation_seq": 42})
    assert gateway.hello["moderation_seq"] == 42


async def test_a_hello_without_a_moderation_head_is_still_accepted(monkeypatch):
    gateway = await open_with(monkeypatch, {"type": "hello", "protocol": 1})
    assert gateway.hello.get("moderation_seq") is None
