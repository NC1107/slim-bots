import json

import pytest

from slimbots import ws as ws_module
from slimbots.ws import Connection


class FakeSocket:
    def __init__(self, incoming, hello_reply=None):
        self.sent = []
        self.closed = False
        self._incoming = list(incoming)
        self._hello_reply = hello_reply if hello_reply is not None else {"type": "hello", "protocol": 1}

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        return json.dumps(self._hello_reply)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for frame in self._incoming:
            yield json.dumps(frame)


class FakeClient:
    def __init__(self, socket):
        self.socket = socket
        self.user_agent = "slimm-bot-test/1.0"

    def ws_ticket(self):
        return "ticket-123"

    def socket_url(self):
        return "wss://my.space/ws"


@pytest.mark.asyncio
async def test_open_sends_hello_with_ticket_and_protocol(monkeypatch):
    socket = FakeSocket(incoming=[])

    async def fake_connect(url, user_agent_header=None):
        assert url == "wss://my.space/ws"
        assert user_agent_header == "slimm-bot-test/1.0"
        return socket

    monkeypatch.setattr(ws_module.websockets, "connect", fake_connect)
    conn = await Connection.open(FakeClient(socket))

    sent = json.loads(socket.sent[0])
    assert sent == {"type": "hello", "ticket": "ticket-123", "protocol": 1}
    await conn.close()
    assert socket.closed


@pytest.mark.asyncio
async def test_open_rejects_a_non_hello_reply(monkeypatch):
    socket = FakeSocket(incoming=[], hello_reply={"type": "error"})

    async def fake_connect(url, user_agent_header=None):
        return socket

    monkeypatch.setattr(ws_module.websockets, "connect", fake_connect)
    with pytest.raises(RuntimeError, match="expected a hello"):
        await Connection.open(FakeClient(socket))
    # the socket must not be left open after a failed handshake
    assert socket.closed


@pytest.mark.asyncio
async def test_frames_yields_parsed_json(monkeypatch):
    frames_in = [{"type": "message.created", "n": 1}, {"type": "unknown.future.event"}]
    socket = FakeSocket(incoming=frames_in)

    async def fake_connect(url, user_agent_header=None):
        return socket

    monkeypatch.setattr(ws_module.websockets, "connect", fake_connect)
    conn = await Connection.open(FakeClient(socket))

    seen = [frame async for frame in conn.frames()]
    assert seen == frames_in


@pytest.mark.asyncio
async def test_context_manager_closes_on_exit(monkeypatch):
    socket = FakeSocket(incoming=[])

    async def fake_connect(url, user_agent_header=None):
        return socket

    monkeypatch.setattr(ws_module.websockets, "connect", fake_connect)
    async with await Connection.open(FakeClient(socket)) as conn:
        assert conn is not None
    assert socket.closed
