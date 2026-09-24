"""The websocket side of the async `Bot`: the hello handshake, reading server frames, and sending client ones."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, AsyncIterator

import websockets

if TYPE_CHECKING:
    from .http import AsyncClient

PROTOCOL = 1


class Gateway:
    """An open, hello-shaken slim-m websocket connection for an `AsyncClient`."""

    def __init__(self, socket: Any) -> None:
        self._socket = socket

    @classmethod
    async def open(cls, client: AsyncClient, *, protocol: int = PROTOCOL) -> Gateway:
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

    async def frames(self) -> AsyncIterator[dict[str, Any]]:
        async for raw in self._socket:
            yield json.loads(raw)

    async def send(self, frame: dict[str, Any]) -> None:
        """Sends one client->server frame (typing, canvas.cursor, canvas.stroke_preview)."""
        await self._socket.send(json.dumps(frame))

    async def close(self) -> None:
        await self._socket.close()

    async def __aenter__(self) -> Gateway:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()
