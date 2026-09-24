"""A thread-offloaded sqlite handle for `bot.data_path`; see docs/framework.md."""

import asyncio
import sqlite3


class Store:
    """Owns one sqlite connection, but every query runs in a worker thread, never on the event loop."""

    def __init__(self, path, *, migrate=None, timeout=30, isolation_level=None):
        self.path = path
        self._migrate = migrate
        self._timeout = timeout
        self._isolation_level = isolation_level
        self._conn = None
        self._lock = asyncio.Lock()

    async def open(self):
        """Opens the connection and runs the migration hook, both off the event loop; safe to call more than once."""
        async with self._lock:
            if self._conn is None:
                self._conn = await asyncio.to_thread(self._open_sync)
        return self

    def _open_sync(self):
        conn = sqlite3.connect(
            self.path, timeout=self._timeout, isolation_level=self._isolation_level, check_same_thread=False
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        if self._migrate is not None:
            self._migrate(conn)
        return conn

    async def run(self, fn, *args, **kwargs):
        """Runs `fn(connection, *args, **kwargs)` in the worker thread and returns its result, one call at a time."""
        async with self._lock:
            return await asyncio.to_thread(fn, self._conn, *args, **kwargs)

    async def close(self):
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    @property
    def connection(self):
        """The raw `sqlite3.Connection`, for a caller not yet moved onto `run()` (a background sweep, a test)."""
        return self._conn
