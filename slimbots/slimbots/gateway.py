"""The websocket side of the async `Bot`: the hello handshake, reading server frames, and sending client ones."""

import json

import websockets

PROTOCOL = 1


class Gateway:
    """An open, hello-shaken slim-m websocket connection for an `AsyncClient`."""

    def __init__(self, socket):
        self._socket = socket

    @classmethod
    async def open(cls, client, *, protocol=PROTOCOL):
        ticket = await client.ws_ticket()
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
        async for raw in self._socket:
            yield json.loads(raw)

    async def send(self, frame):
        """Sends one client->server frame (typing, canvas.cursor, canvas.stroke_preview)."""
        await self._socket.send(json.dumps(frame))

    async def close(self):
        await self._socket.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
