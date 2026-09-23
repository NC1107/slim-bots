"""The websocket side of a bot: mint a ticket, say hello, read frames.

`Connection` is deliberately thin. It does the hello handshake once and then
gets out of the way - a frame this bot's caller does not recognise is that
caller's job to ignore, not this module's, because a new event type must not
break a bot that has never heard of it (see docs/bots/building-bots.md).
"""

import json

import websockets

PROTOCOL = 1


class Connection:
    """An open, hello-shaken slim-m websocket connection.

    Use it as an async context manager:

        async with await Connection.open(client) as conn:
            async for frame in conn.frames():
                ...
    """

    def __init__(self, socket):
        self._socket = socket

    @classmethod
    async def open(cls, client, *, protocol=PROTOCOL):
        ticket = client.ws_ticket()
        socket = await websockets.connect(client.socket_url(), user_agent_header=client.user_agent)
        try:
            await socket.send(json.dumps({"type": "hello", "ticket": ticket, "protocol": protocol}))
            hello = json.loads(await socket.recv())
            if hello.get("type") != "hello":
                raise RuntimeError(f"expected a hello back, got {hello}")
        except BaseException:
            await socket.close()
            raise
        return cls(socket)

    async def frames(self):
        """Yields each frame as parsed JSON, in arrival order."""
        async for raw in self._socket:
            yield json.loads(raw)

    async def close(self):
        await self._socket.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
