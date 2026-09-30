"""A minimal async Source RCON client, the protocol Minecraft's server uses; the password never appears in an error."""

import asyncio
import struct

TYPE_COMMAND = 2
TYPE_LOGIN = 3
AUTH_FAILED_ID = -1
MAX_PACKET = 4110
TIMEOUT_SECONDS = 8


class RconError(Exception):
    """An RCON call that failed; `str(self)` is safe to log and never carries the password."""


class RconAuthError(RconError):
    """The server rejected the password."""


def encode(request_id, kind, body):
    payload = struct.pack("<ii", request_id, kind) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


async def read_packet(reader):
    """One (id, type, body) packet off the stream."""
    (length,) = struct.unpack("<i", await reader.readexactly(4))
    if length < 10 or length > MAX_PACKET:
        raise RconError(f"rcon packet of {length} bytes is out of range")
    data = await reader.readexactly(length)
    request_id, kind = struct.unpack("<ii", data[:8])
    return request_id, kind, data[8:-2].decode("utf-8", errors="replace")


class RconClient:
    """Connects lazily, serialises commands, and reconnects on the next call after any failure."""

    def __init__(self, host, port, password):
        self._host = host
        self._port = port
        self._password = password
        self._reader = None
        self._writer = None
        self._next_id = 0
        self._lock = asyncio.Lock()

    def _request_id(self):
        self._next_id = self._next_id % 0x7FFFFFF0 + 1
        return self._next_id

    async def _connect(self):
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        request_id = self._request_id()
        self._writer.write(encode(request_id, TYPE_LOGIN, self._password))
        await self._writer.drain()
        reply_id, _kind, _body = await read_packet(self._reader)
        if reply_id == AUTH_FAILED_ID:
            raise RconAuthError("the rcon password was rejected")

    def close(self):
        if self._writer is not None:
            self._writer.close()
        self._reader = self._writer = None

    async def command(self, text):
        """The server's reply to one command; a transport failure raises RconError after dropping the connection."""
        async with self._lock:
            try:
                return await asyncio.wait_for(self._command(text), TIMEOUT_SECONDS)
            except RconError:
                self.close()
                raise
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, struct.error) as err:
                self.close()
                raise RconError(f"rcon {self._host}:{self._port} unreachable ({type(err).__name__})") from None

    async def _command(self, text):
        if self._writer is None:
            await self._connect()
        request_id = self._request_id()
        self._writer.write(encode(request_id, TYPE_COMMAND, text))
        await self._writer.drain()
        reply_id, _kind, body = await read_packet(self._reader)
        if reply_id == AUTH_FAILED_ID:
            raise RconAuthError("the rcon password was rejected")
        return body
