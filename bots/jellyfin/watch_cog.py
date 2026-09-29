"""bot-jellyfin's `!watch`/`!pause`/`!resume`/`!seek`/`!np`/`!stop`/`!subs` watch-party commands; see README.md."""

from __future__ import annotations

import asyncio

from slimbots import Permissions
from slimbots.limits import ValidationError, require_len
from slimbots.voice import VoiceError

import jellyfin_core
import playback_progress
import quality
from stream_session import StreamError, WatchSession, format_hms, parse_hms

MAX_PICK_RESULTS = 5
PICK_TIMEOUT_SECONDS = 30
RESUME_WORDS = {"resume", "yes", "y"}
START_OVER_WORDS = {"start", "restart", "no", "n"}

_active_session: WatchSession | None = None


def active_session():
    return _active_session


def _set_active_session(session):
    global _active_session
    _active_session = session


def _channel_name(bot, channel_id):
    channel = bot.space.channels.get(channel_id)
    return f"#{channel.name}" if channel is not None else channel_id


async def _pick_result(ctx, items):
    """Lists up to `MAX_PICK_RESULTS` matches and waits for the invoker's numeric reply, or None on a bad/late one."""
    shown = items[:MAX_PICK_RESULTS]
    lines = [f"{i + 1}. {item.get('Name')} ({item.get('ProductionYear', '?')})" for i, item in enumerate(shown)]
    await ctx.reply("multiple matches - reply with a number:\n" + "\n".join(lines))

    def is_a_number_reply(message):
        same_place = message.get("channel_id") == ctx.channel_id and message.get("author_id") == ctx.author.id
        return same_place and (message.get("content") or "").strip().isdigit()

    try:
        message = await ctx.bot.wait_for("on_raw_message", check=is_a_number_reply, timeout=PICK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await ctx.reply("timed out - `!watch` again to retry.")
        return None
    index = int(message["content"].strip()) - 1
    if not 0 <= index < len(shown):
        await ctx.reply("that wasn't one of the listed numbers.")
        return None
    return shown[index]


def _may_control(ctx, session):
    return ctx.author.id == session.started_by_id or ctx.author.has_permission(Permissions.MANAGE_CHANNELS)


async def _offer_resume(ctx, title, position):
    """Asks whether to pick up at `position`; the start second to use, or None when the invoker did not answer."""
    await ctx.reply(
        f"**{title}** is at {format_hms(position)} - reply `resume` to pick up there, or `start` to begin from the start."
    )

    def is_an_answer(message):
        same_place = message.get("channel_id") == ctx.channel_id and message.get("author_id") == ctx.author.id
        return same_place and (message.get("content") or "").strip().lower() in RESUME_WORDS | START_OVER_WORDS

    try:
        message = await ctx.bot.wait_for("on_raw_message", check=is_an_answer, timeout=PICK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await ctx.reply("timed out - `!watch` again to retry.")
        return None
    return position if message["content"].strip().lower() in RESUME_WORDS else 0.0


async def _find_item(ctx, query):
    """The playable item for `query`, or the account's last unfinished one when `query` is empty; replies and returns None otherwise."""
    if not query.strip():
        last = await asyncio.to_thread(playback_progress.fetch_last_watched)
        if last is None:
            await ctx.reply("nothing to resume - `!watch <title>` to pick something.")
        return last
    try:
        query = require_len(query.strip(), max_len=jellyfin_core.MAX_QUERY_LENGTH, field="a title")
    except ValidationError as err:
        await ctx.reply(str(err))
        return None
    results = await asyncio.to_thread(jellyfin_core.search_items, query, jellyfin_core.MAX_SEARCH_RESULTS)
    playable = [item for item in results if item.get("Type") in ("Movie", "Episode")]
    if not playable:
        await ctx.reply(f'nothing playable found for "{query}".')
        return None
    return playable[0] if len(playable) == 1 else await _pick_result(ctx, playable)


async def _load_for_playback(ctx, item):
    user_id = await asyncio.to_thread(playback_progress.resolve_user_id)
    full_item = await asyncio.to_thread(jellyfin_core.fetch_item_for_playback, item["Id"], user_id)
    if full_item is None:
        await ctx.reply("could not load that title from jellyfin.")
    return full_item


async def run_watch(ctx, query):
    if _active_session is not None and not _active_session.finished:
        await ctx.reply(f"already watching **{_active_session.title}** in this deployment - `!stop` it first.")
        return
    if not ctx.channel_id:
        return
    voice_channel_id = await ctx.bot.voice.find_member(ctx.author.id)
    if voice_channel_id is None:
        await ctx.reply("join a voice channel first, then run `!watch` again.")
        return
    try:
        item = await _find_item(ctx, query)
        full_item = await _load_for_playback(ctx, item) if item is not None else None
    except jellyfin_core.JellyfinAuthError:
        await ctx.reply("jellyfin is unavailable right now.")
        return
    if full_item is None:
        return
    duration_seconds = (full_item.get("RunTimeTicks") or 0) / playback_progress.TICKS_PER_SECOND
    start_seconds = playback_progress.saved_position_seconds(full_item, duration_seconds)
    if start_seconds:
        start_seconds = await _offer_resume(ctx, full_item.get("Name") or "this title", start_seconds)
        if start_seconds is None:
            return
    voice_channel_name = _channel_name(ctx.bot, voice_channel_id)
    try:
        voice_session = await ctx.bot.voice.join(voice_channel_id)
    except VoiceError as err:
        await ctx.reply(f"can't join {voice_channel_name}: {err}")
        return
    if not voice_session.can_publish:
        await voice_session.leave()
        await ctx.reply(f"I can join {voice_channel_name} but can't speak there - I need SPEAK to stream video/audio.")
        return
    session = WatchSession(ctx.bot, ctx.channel_id, voice_channel_id, full_item, ctx.author.id, voice_session)
    try:
        await session.start(start_seconds)
    except (StreamError, VoiceError) as err:
        await voice_session.leave()
        await ctx.reply(f"could not start streaming: {err}")
        return
    _set_active_session(session)
    resumed = f", from {format_hms(start_seconds)}" if start_seconds else ""
    await ctx.reply(f"streaming **{session.title}** into {voice_channel_name} ({format_hms(session.duration_seconds)}{resumed}).")


async def run_pause(ctx):
    session = _active_session
    if session is None or session.finished:
        await ctx.reply("nothing is playing.")
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can pause it.")
        return
    if not session.pause():
        await ctx.reply("already paused.")
        return
    await ctx.reply("paused.")


async def run_resume(ctx):
    session = _active_session
    if session is None or session.finished:
        await ctx.reply("nothing is playing.")
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can resume it.")
        return
    if not session.resume():
        await ctx.reply("already playing.")
        return
    await ctx.reply("resumed.")


async def run_seek(ctx, position_text):
    session = _active_session
    if session is None or session.finished:
        await ctx.reply("nothing is playing.")
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can seek it.")
        return
    try:
        seconds = parse_hms(position_text or "")
    except ValueError:
        await ctx.reply("give a time like `1:02:03`, `2:03`, or a bare second count.")
        return
    await session.seek(seconds)
    await ctx.reply(f"seeked to {format_hms(session.position_seconds)}.")


async def run_stop(ctx):
    session = _active_session
    if session is None or session.finished:
        await ctx.reply("nothing is playing.")
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can stop it.")
        return
    await session.stop(reason=f"stopped by {ctx.author.display_name}")


async def run_now_playing(ctx):
    session = _active_session
    if session is None or session.finished:
        await ctx.reply("nothing is playing.")
        return
    await ctx.reply(embed=session.now_playing_embed())


async def run_subs(ctx, language):
    session = _active_session
    if session is None or session.finished:
        await ctx.reply("nothing is playing.")
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can change subtitles.")
        return
    language = (language or "").strip()
    if not language or language.lower() == "off":
        await session.set_subtitle(None, None)
        await ctx.reply("subtitles off.")
        return
    stream = jellyfin_core.find_subtitle_stream(session.item, language)
    if stream is None:
        await ctx.reply(f'no subtitle track matching "{language}".')
        return
    await session.set_subtitle(stream["Index"], stream.get("DisplayTitle") or stream.get("Language") or language)
    await ctx.reply(f"subtitles set to {session.subtitle_label}.")


async def run_quality(ctx, preset_name):
    session = _active_session
    if session is None or session.finished:
        await ctx.reply("nothing is playing.")
        return
    preset_name = (preset_name or "").strip()
    if not preset_name:
        await ctx.reply(f"quality is {session.quality.describe()}. change it with `!quality <{'|'.join(quality.PRESETS)}>`.")
        return
    preset = quality.find_preset(preset_name)
    if preset is None:
        await ctx.reply(f'no quality called "{preset_name}" - try {quality.preset_names()}.')
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can change the quality.")
        return
    try:
        await session.set_quality(preset)
    except (StreamError, VoiceError) as err:
        await ctx.reply(f"could not switch quality: {err}")
        return
    note = f" {quality.HEAVY_WARNING}" if preset.is_heavy else ""
    await ctx.reply(f"quality set to {preset.describe()}, resumed at {format_hms(session.position_seconds)}.{note}")


def setup(bot):
    @bot.command(name="watch", help="Start a watch party in your voice channel; with no title, offers the last thing you were watching", usage="[title]")
    async def watch(ctx, query: str = ""):
        await run_watch(ctx, query)

    @bot.command(name="pause", help="Pause the current watch party")
    async def pause(ctx):
        await run_pause(ctx)

    @bot.command(name="resume", help="Resume the current watch party")
    async def resume(ctx):
        await run_resume(ctx)

    @bot.command(name="seek", help="Jump to a position in the current watch party", usage="<h:mm:ss>")
    async def seek(ctx, position: str = ""):
        await run_seek(ctx, position)

    @bot.command(name="stop", help="Stop the current watch party")
    async def stop(ctx):
        await run_stop(ctx)

    @bot.command(name="np", help="Show what's currently playing")
    async def now_playing(ctx):
        await run_now_playing(ctx)

    @bot.command(name="subs", help="Set or turn off burned-in subtitles", usage="<lang|off>")
    async def subs(ctx, language: str = ""):
        await run_subs(ctx, language)

    @bot.command(name="quality", help="Show or change the stream quality", usage="[low|medium|high]")
    async def quality_command(ctx, preset: str = ""):
        await run_quality(ctx, preset)

    @bot.event
    async def on_voice_activity(event):
        session = _active_session
        if session is not None and not session.finished and event.channel_id == session.voice_channel_id:
            session.wake_monitor()
