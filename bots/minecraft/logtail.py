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
        self._skipping = False
        self._seen = False

    def read_new(self):
        try:
            stat = os.stat(self._path)
        except OSError:
            self._offset = self._identity = None
            self._partial, self._skipping = b"", False
            return []
        identity = (stat.st_dev, stat.st_ino)
        if self._offset is None:
            # A log that returns after being seen is a fresh one, so nothing in it is history.
            self._offset, self._identity = (0 if self._seen else stat.st_size), identity
            self._seen = True
        if identity != self._identity or stat.st_size < self._offset:
            self._offset, self._identity, self._partial, self._skipping = 0, identity, b"", False
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
        *complete, rest = (self._partial + chunk).split(b"\n")
        lines = []
        for line in complete:
            if self._skipping:
                self._skipping = False
            elif len(line) <= MAX_LINE_BYTES:
                lines.append(line.decode("utf-8", errors="replace"))
        self._skipping = self._skipping or len(rest) > MAX_LINE_BYTES
        self._partial = b"" if self._skipping else rest
        return lines
