"""Tells the call where the party is: the server's durable watch session and its 5 second tick (slim-m decision 0050).
The panel message is a separate surface and is not touched here."""

from __future__ import annotations

import asyncio
import logging

from slimbots import ApiError

log = logging.getLogger("jellyfin.watch_sync")

TICK_SECONDS = 5.0
# The server refills one write per 2 seconds after a burst of 4, so a mashed control must not outrun it.
MIN_GAP_SECONDS = 2.0


class WatchSync:
    """One worker per party: a state change becomes a PUT, a quiet interval becomes a tick, and the two never race."""

    def __init__(self, session):
        self.session = session
        self._wake = asyncio.Event()
        self._dirty = True
        self._seeked = False
        self._controller = session.started_by_id
        self._task = None
        self.gave_up = False

    def start(self):
        self._task = self.session.bot.background(self._run(), name=f"jellyfin-watch-sync-{self.session.voice_channel_id}")

    def changed(self, *, seeked=False):
        """A play, pause, seek or new title: states the session again at once, folding bursts into one write."""
        self._dirty = True
        self._seeked = self._seeked or seeked
        self._wake.set()

    async def end(self):
        """Stops the worker and ends the session on the server; a refusal is logged, never raised."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self.gave_up:
            return
        try:
            await self.session.bot.client.end_watch_session(self.session.voice_channel_id)
        except ApiError as err:
            log.warning("could not end the watch session in %s: %s", self.session.voice_channel_id, err)

    async def _run(self):
        loop = asyncio.get_running_loop()
        last_sent = loop.time() - TICK_SECONDS
        while not self.gave_up:
            if not self._dirty:
                await self._wait_for_change(last_sent + TICK_SECONDS - loop.time())
            self._wake.clear()
            last_sent = loop.time()
            await self._send()
            if self._dirty and not self.gave_up:
                await self._wait_for_change(TICK_SECONDS)
            await asyncio.sleep(MIN_GAP_SECONDS)

    async def _wait_for_change(self, timeout):
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, timeout))
        except asyncio.TimeoutError:
            pass

    async def _send(self):
        if self._dirty:
            seeked, self._seeked, self._dirty = self._seeked, False, False
            try:
                await self._put(seeked)
            except ApiError as err:
                self._handle_refusal(err, seeked_was=seeked)
            return
        try:
            await self._tick()
        except ApiError as err:
            self._handle_refusal(err, seeked_was=False)

    def _sample(self):
        session = self.session
        return session.voice_channel_id, not session.paused, int(session.position_seconds * 1000)

    async def _put(self, seeked):
        session = self.session
        channel_id, playing, position_ms = self._sample()
        await session.bot.client.set_watch_session(
            channel_id, item_id=session.item_id, title=session.title, playing=playing, position_ms=position_ms,
            duration_ms=int(session.duration_seconds * 1000) or None, seeked=seeked, controller_user_id=self._controller,
        )

    async def _tick(self):
        channel_id, playing, position_ms = self._sample()
        await self.session.bot.client.tick_watch_session(channel_id, playing=playing, position_ms=position_ms)

    def _handle_refusal(self, err, *, seeked_was):
        """404 on a tick means the server let the session lapse, so state it afresh; 400 and 409 will not pass, so stop."""
        channel_id = self.session.voice_channel_id
        if err.status == 400 and self._controller is not None:
            log.warning("the server refused controller_user_id in %s (%s); stating the session without one", channel_id, err.reason)
            self._controller = None
            self._retry(seeked_was)
        elif err.status in (400, 409):
            log.warning("not syncing the watch position in %s: %s", channel_id, err.reason or err)
            self.gave_up = True
        else:
            log.warning("watch sync write failed in %s: %s", channel_id, err)
            self._retry(seeked_was)

    def _retry(self, seeked_was):
        self._dirty = True
        self._seeked = self._seeked or seeked_was
