"""bot-jellyfin's `!watch`/`!pause`/`!resume`/`!seek`/`!np`/`!stop`/`!subs` watch-party commands; see README.md."""

from __future__ import annotations

import asyncio

from slimbots import Permissions
from slimbots.limits import ValidationError, require_len
from slimbots.voice import VoiceError

import jellyfin_core
from stream_session import StreamError, WatchSession, format_hms, parse_hms

MAX_PICK_RESULTS = 5
PICK_TIMEOUT_SECONDS = 30

_active_session: WatchSession | None = None


def active_session():
    return _active_session


def _set_active_session(session):
    global _active_session
    _active_session = session


async def _invoker_is_in_the_call(ctx):
    roster = await ctx.bot.client.voice_roster(ctx.channel_id)
    participant_ids = {p.get("user_id") for p in roster.get("participants", [])}
    return ctx.author.id in participant_ids


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


async def run_watch(ctx, query):
    if _active_session is not None and not _active_session.finished:
        await ctx.reply(f"already watching **{_active_session.title}** in this deployment - `!stop` it first.")
        return
    if not ctx.channel_id:
        return
    try:
        query = require_len(query.strip(), max_len=jellyfin_core.MAX_QUERY_LENGTH, field="a title")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    if not await _invoker_is_in_the_call(ctx):
        await ctx.reply("join this channel's voice call first.")
        return
    try:
        results = await asyncio.to_thread(jellyfin_core.search_items, query, jellyfin_core.MAX_SEARCH_RESULTS)
    except jellyfin_core.JellyfinAuthError:
        await ctx.reply("jellyfin is unavailable right now.")
        return
    playable = [item for item in results if item.get("Type") in ("Movie", "Episode")]
    if not playable:
        await ctx.reply(f'nothing playable found for "{query}".')
        return
    item = playable[0]
    if len(playable) > 1:
        picked = await _pick_result(ctx, playable)
        if picked is None:
            return
        item = picked
    full_item = await asyncio.to_thread(jellyfin_core.fetch_item_for_playback, item["Id"])
    if full_item is None:
        await ctx.reply("could not load that title from jellyfin.")
        return
    try:
        voice_session = await ctx.bot.voice.join(ctx.channel_id)
    except VoiceError as err:
        await ctx.reply(f"could not join the call: {err}")
        return
    session = WatchSession(ctx.bot, ctx.channel_id, full_item, ctx.author.id, voice_session)
    try:
        await session.start()
    except (StreamError, VoiceError) as err:
        await voice_session.leave()
        await ctx.reply(f"could not start streaming: {err}")
        return
    _set_active_session(session)
    await ctx.reply(f"now watching **{session.title}** ({format_hms(session.duration_seconds)}).")


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


def setup(bot):
    @bot.command(name="watch", help="Search Jellyfin and start a watch party in this voice channel", usage="<title>")
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

    @bot.event
    async def on_voice_activity(event):
        session = _active_session
        if session is not None and not session.finished and event.channel_id == session.channel_id:
            session.wake_monitor()
