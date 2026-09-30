"""Follows a server log that may not exist yet and is truncated or replaced on every restart."""

import os

MAX_READ_BYTES = 1 << 20
MAX_LINE_BYTES = 8192


class LogTail:
    """`read_new()` returns the complete lines appended since the last call; it starts at the end, never replaying history."""

    def __init__(self, path):
        self._path = path
        self._offset = None
        self._identity = None
        self._partial = b""

    def read_new(self):
        try:
            stat = os.stat(self._path)
        except OSError:
            self._offset = self._identity = None
            return []
        identity = (stat.st_dev, stat.st_ino)
        if self._offset is None:
            self._offset, self._identity = stat.st_size, identity
            return []
        if identity != self._identity or stat.st_size < self._offset:
            self._offset, self._identity, self._partial = 0, identity, b""
        if stat.st_size == self._offset:
            return []
        return self._read_from_offset()

    def _read_from_offset(self):
        try:
            with open(self._path, "rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(MAX_READ_BYTES)
        except OSError:
            return []
        self._offset += len(chunk)
        *complete, self._partial = (self._partial + chunk).split(b"\n")
        self._partial = self._partial[-MAX_LINE_BYTES:]
        return [line.decode("utf-8", errors="replace") for line in complete if len(line) <= MAX_LINE_BYTES]
