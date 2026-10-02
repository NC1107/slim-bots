"""The now-playing panel: one message per watch party whose text and buttons follow the stream's state."""

from __future__ import annotations

import contextlib

from slimbots import ApiError, Button, rows

import quality
from stream_session import format_hms

SKIP_SECONDS = 30
ID_PREFIX = "jf:"


def _state_text(session):
    if session.waiting_next is not None:
        return f"next up: {session.waiting_next.get('Name')}"
    if session.paused:
        return f"paused {format_hms(session.position_seconds)}"
    return None


def panel_text(session, voice_name, *, ended_reason=None):
    """One short line; the position is only shown while paused, since the panel is not redrawn as it advances."""
    if ended_reason or session.finished:
        return f"{session.title} - ended"
    parts = [session.title, _state_text(session)]
    if session.duration_seconds:
        parts.append(format_hms(session.duration_seconds))
    parts.append(f"{session.quality.height}p")
    if session.subtitle_label:
        parts.append(f"subtitles {session.subtitle_label}")
    return " - ".join(p for p in parts if p)


def _preset_name(session):
    current = session.quality
    for preset in quality.PRESETS.values():
        if (preset.width, preset.height) == (current.width, current.height):
            return preset.name
    return None


def panel_rows(session, *, live=True):
    """Every button is disabled once nothing is playing; between episodes only Next episode and Stop work."""
    off = not live
    between = live and session.waiting_next is not None
    playing = _preset_name(session)
    quality_row = [
        Button(f"{p.height}p", f"{ID_PREFIX}q:{p.name}", style="primary" if p.name == playing else "secondary",
               disabled=off or between or p.name == playing)
        for p in quality.PRESETS.values()
    ]
    transport = [
        Button("Play" if session.paused else "Pause", f"{ID_PREFIX}toggle", style="primary", disabled=off or between),
        Button(f"-{SKIP_SECONDS}s", f"{ID_PREFIX}back", disabled=off or between),
        Button(f"+{SKIP_SECONDS}s", f"{ID_PREFIX}fwd", disabled=off or between),
        Button("Stop", f"{ID_PREFIX}stop", style="danger", disabled=off),
    ]
    subs_on = session.subtitle_stream_index is not None
    options = quality_row + [
        Button("Subtitles", f"{ID_PREFIX}subs", style="primary" if subs_on else "secondary", disabled=off or between),
    ]
    if session.item.get("Type") == "Episode":
        options.append(Button("Next episode", f"{ID_PREFIX}next", style="primary" if between else "secondary", disabled=off))
    return rows(transport, options)


class Panel:
    """Posts and keeps up to date the one message whose buttons drive a party."""

    def __init__(self, bot, channel_id, voice_name):
        self.bot = bot
        self.channel_id = channel_id
        self.voice_name = voice_name
        self.message_id = None
        self._last = None

    async def post(self, session, reply_to_id=None):
        assert self.bot.client is not None
        message = await self.bot.client.send(
            self.channel_id, panel_text(session, self.voice_name), reply_to_id=reply_to_id, components=panel_rows(session),
        )
        self.message_id = message.id
        self._last = (panel_text(session, self.voice_name), panel_rows(session))

    async def refresh(self, session, *, ended_reason=None):
        """Best-effort: a failed edit must never interrupt playback."""
        if self.message_id is None:
            return
        live = ended_reason is None and not session.finished
        text, layout = panel_text(session, self.voice_name, ended_reason=ended_reason), panel_rows(session, live=live)
        previous_text, previous_layout = self._last or (None, None)
        with contextlib.suppress(ApiError):
            if text != previous_text:
                await self.bot.client.edit_message(self.channel_id, self.message_id, text)
            if layout != previous_layout:
                await self.bot.client.edit_components(self.channel_id, self.message_id, layout)
            self._last = (text, layout)

    async def close(self, session, reason):
        await self.refresh(session, ended_reason=reason)
