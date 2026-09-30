"""The stateful middle of the bridge: who is online, what reaches slim-m and when, and the per-minute caps."""

import time
from collections import deque

from mc_core import is_relay_echo, format_for_slim

WINDOW_SECONDS = 60.0
MAX_POST_CHARS = 1800


class MinuteCap:
    """Allows at most `limit` events in any sliding minute."""

    def __init__(self, limit, clock=time.monotonic):
        self._limit = limit
        self._clock = clock
        self._stamps = deque()

    def allow(self):
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= WINDOW_SECONDS:
            self._stamps.popleft()
        if len(self._stamps) >= self._limit:
            return False
        self._stamps.append(now)
        return True


class Batcher:
    """Collects channel lines and releases them as a few joined posts; overflow is dropped and counted, never queued forever."""

    def __init__(self, interval, max_lines, max_posts_per_minute, queue_max, clock=time.monotonic):
        self._interval = interval
        self._max_lines = max_lines
        self._queue_max = queue_max
        self._clock = clock
        self._posts = MinuteCap(max_posts_per_minute, clock)
        self._lines = deque()
        self._dropped = 0
        self._last_flush = clock()

    def add(self, line):
        if len(self._lines) >= self._queue_max:
            self._lines.popleft()
            self._dropped += 1
        self._lines.append(line)

    def take(self):
        """The next post's text, or None while it is too soon, the queue is empty, or the post cap is spent."""
        now = self._clock()
        if not self._lines or now - self._last_flush < self._interval or not self._posts.allow():
            return None
        self._last_flush = now
        chosen = []
        size = 0
        while self._lines and len(chosen) < self._max_lines and size + len(self._lines[0]) + 1 <= MAX_POST_CHARS:
            size += len(self._lines[0]) + 1
            chosen.append(self._lines.popleft())
        if not chosen:
            chosen.append(self._lines.popleft()[:MAX_POST_CHARS])
        if self._dropped:
            chosen.append(f"_{self._dropped} earlier lines were dropped: the server is faster than the rate limit._")
            self._dropped = 0
        return "\n".join(chosen)


class Bridge:
    """Turns parsed log events into channel lines, keeping the set of players known to be online."""

    def __init__(self, announce, batcher):
        self._announce = set(announce)
        self._batcher = batcher
        self.online = set()

    def handle(self, event):
        """Queues the event for slim-m when it is allowed; returns True if it was queued."""
        if event.kind == "death" and event.player not in self.online:
            return False
        if event.kind == "chat" and is_relay_echo(event):
            return False
        self._track(event)
        if event.kind not in self._announce:
            return False
        self._batcher.add(format_for_slim(event))
        return True

    def _track(self, event):
        if event.kind == "leave":
            self.online.discard(event.player)
        elif event.kind in ("join", "chat", "advancement"):
            self.online.add(event.player)
