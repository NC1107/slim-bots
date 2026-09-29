"""Playback controls shared by the now-playing panel's buttons and, later, the call dock; see README.md."""

from __future__ import annotations

import asyncio
import contextlib

from slimbots import ApiError
from slimbots.voice import VoiceError

import jellyfin_core
import playback_progress
import quality
import session_registry
from panel import ID_PREFIX, SKIP_SECONDS
from stream_session import StreamError


def preflight(session, action):
    """A reason the action cannot run right now, else None; checked before the press is acknowledged."""
    if session.waiting_next is not None and action not in ("next", "stop"):
        return "that episode has finished - press Next episode, or Stop."
    if action == "subs" and session.subtitle_stream_index is None and not jellyfin_core.subtitle_streams(session.item):
        return "this title has no subtitle tracks."
    if action.startswith("q:") and quality.find_preset(action[2:]) is None:
        return "that quality does not exist."
    return None


def _default_subtitle(item):
    tracks = jellyfin_core.subtitle_streams(item)
    preferred = [t for t in tracks if (t.get("Language") or "").lower() in ("eng", "en")]
    return (preferred or tracks)[0]


async def _next_episode(session):
    user_id = session.jellyfin_user_id or await asyncio.to_thread(playback_progress.resolve_user_id)
    return await asyncio.to_thread(jellyfin_core.next_episode, session.item, user_id)


async def _play_next(session):
    upcoming = session.waiting_next or await _next_episode(session)
    if upcoming is None:
        return "that was the last episode."
    await session.play_item(upcoming)
    return None


async def apply(session, action, who="a member"):
    """Runs one control against a party; a sentence for the member when it cannot, else None."""
    if action == "toggle":
        if session.paused:
            session.resume()
        else:
            session.pause()
    elif action in ("back", "fwd"):
        step = -SKIP_SECONDS if action == "back" else SKIP_SECONDS
        await session.seek(session.position_seconds + step)
    elif action == "stop":
        await session.stop(reason=f"stopped by {who}")
    elif action.startswith("q:"):
        await session.set_quality(quality.PRESETS[action[2:]])
    elif action == "subs":
        if session.subtitle_stream_index is not None:
            await session.set_subtitle(None, None)
        else:
            track = _default_subtitle(session.item)
            await session.set_subtitle(track["Index"], track.get("DisplayTitle") or track.get("Language") or "on")
    elif action == "next":
        return await _play_next(session)
    return None


async def _refuse(interaction, text):
    with contextlib.suppress(ApiError):
        await interaction.reply_ephemeral(text)


async def run_control(interaction, session, action):
    """Gate on being in the call, answer the press, then act and redraw the panel."""
    bot = interaction.bot
    voice_channel_id = await bot.voice.find_member(interaction.user_id)
    if voice_channel_id != session.voice_channel_id:
        name = session_registry.channel_name(bot, session.voice_channel_id)
        await _refuse(interaction, f"join {name} to use these controls.")
        return
    problem = preflight(session, action)
    if problem:
        await _refuse(interaction, problem)
        return
    await interaction.ack()
    async with session.control_lock:
        if session.finished:
            return
        try:
            problem = await apply(session, action, interaction.user_display_name or "a member")
        except (StreamError, VoiceError) as err:
            problem = f"that did not work: {err}"
        if problem:
            await bot.client.send(session.text_channel_id, problem)
    if not session.finished:
        await session.refresh_panel()


async def on_panel_press(interaction):
    session = session_registry.session_for_panel(interaction.message_id)
    if session is None:
        await _refuse(interaction, "that watch party has ended - `!watch` starts another.")
        return
    await run_control(interaction, session, interaction.custom_id[len(ID_PREFIX):])


CALL_CONTROLS = (
    ("jf-playpause", "Play or pause", "pause", "toggle"),
    ("jf-back", f"Back {SKIP_SECONDS}s", "skip_previous", "back"),
    ("jf-forward", f"Forward {SKIP_SECONDS}s", "skip_next", "fwd"),
    ("jf-stop", "Stop", "stop", "stop"),
)


def call_control_handler(action):
    """A dock control acts on the party in the call it was used in, through the same path as a panel button."""

    async def handler(interaction):
        bot = interaction.bot
        session = session_registry.session_for_channel(interaction.channel_id)
        if session is None:
            voice_channel_id = await bot.voice.find_member(interaction.user_id)
            session = session_registry.session_for_channel(voice_channel_id)
        if session is None:
            await _refuse(interaction, "nothing is playing in your call.")
            return
        await run_control(interaction, session, action)

    return handler
