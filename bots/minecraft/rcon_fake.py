"""A real local TCP server speaking the RCON wire format, for tests; records every command it is sent."""

import asyncio
import struct

from rcon import encode, read_packet


class FakeRcon:
    def __init__(self, password="pw", list_reply="There are 2 of a max of 20 players online: Steve, Alex"):
        self.password = password
        self.list_reply = list_reply
        self.commands = []
        self.logins = 0
        self._server = None
        self._writers = []

    async def start(self):
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self):
        if self._server is None:
            return
        self._server.close()
        for writer in self._writers:
            writer.close()
        await self._server.wait_closed()
        self._server = None

    async def _serve(self, reader, writer):
        self._writers.append(writer)
        try:
            while True:
                request_id, kind, body = await read_packet(reader)
                if kind == 3:
                    self.logins += 1
                    ok = body == self.password
                    writer.write(encode(request_id if ok else -1, 2, ""))
                else:
                    self.commands.append(body)
                    reply = self.list_reply if body == "list" else ""
                    writer.write(encode(request_id, 0, reply))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, struct.error):
            pass
        finally:
            writer.close()
