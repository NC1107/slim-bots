"""How `!watch` finds a title, asks what to do about it, and starts the party; see README.md."""

from __future__ import annotations

import asyncio
from functools import partial

from slimbots.limits import ValidationError, require_len
from slimbots.voice import VoiceError

import jellyfin_core
import panel
import picker
import playback_progress
import session_registry
from stream_session import StreamError, WatchSession

PLAYABLE = ("Movie", "Episode")


async def _refuse_if_busy(ctx, voice_channel_id):
    """One party per call: the bot is one participant there, so a second share would read as the same person's."""
    running = session_registry.session_for_channel(voice_channel_id)
    if running is None:
        return False
    name = session_registry.channel_name(ctx.bot, voice_channel_id)
    await ctx.reply(f"already watching **{running.title}** in {name} - `!stop` it first. another call can have its own stream.")
    return True


async def _find_item(ctx, query):
    """The item to play for `query`; None after a reply or once a button chooser has taken over."""
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
    results = await asyncio.to_thread(jellyfin_core.watch_search, query, jellyfin_core.MAX_SEARCH_RESULTS)
    if not results:
        await ctx.reply(f'nothing playable found for "{query}".')
        return None
    if len(results) == 1 and results[0].get("Type") in PLAYABLE:
        return results[0]
    chooser = picker.Pick(
        invoker_id=ctx.author.id, invoker_name=ctx.author.display_name, channel_id=ctx.channel_id,
        on_choose=partial(begin, ctx), items=results[: picker.MAX_MATCHES], query=query,
    )
    await picker.open_pick(ctx.bot, chooser, reply_to_id=ctx.message.get("id"))
    return None


async def _load_for_playback(ctx, item):
    user_id = await asyncio.to_thread(playback_progress.resolve_user_id)
    full_item = await asyncio.to_thread(jellyfin_core.fetch_item_for_playback, item["Id"], user_id)
    if full_item is None:
        await ctx.reply("could not load that title from jellyfin.")
    return full_item


async def begin(ctx, item):
    """Loads the item, and asks resume-or-start-over when the account has a saved position for it."""
    try:
        full_item = await _load_for_playback(ctx, item)
    except jellyfin_core.JellyfinAuthError:
        await ctx.reply("jellyfin is unavailable right now.")
        return
    if full_item is None:
        return
    duration_seconds = (full_item.get("RunTimeTicks") or 0) / playback_progress.TICKS_PER_SECOND
    saved = playback_progress.saved_position_seconds(full_item, duration_seconds)
    if not saved:
        await launch(ctx, full_item, 0.0)
        return
    question = picker.Pick(
        invoker_id=ctx.author.id, invoker_name=ctx.author.display_name, channel_id=ctx.channel_id,
        on_choose=partial(launch, ctx, full_item), view="resume", resume_seconds=saved,
        title=full_item.get("Name") or "this title",
    )
    await picker.open_pick(ctx.bot, question, reply_to_id=ctx.message.get("id"))


async def launch(ctx, full_item, start_seconds):
    """Joins the invoker's call as it is now and starts the stream, with the panel as the reply."""
    voice_channel_id = await ctx.bot.voice.find_member(ctx.author.id)
    if voice_channel_id is None:
        await ctx.reply("join a voice channel first, then run `!watch` again.")
        return
    if await _refuse_if_busy(ctx, voice_channel_id):
        return
    voice_channel_name = session_registry.channel_name(ctx.bot, voice_channel_id)
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
    session_registry.add(session)
    session.panel = panel.Panel(ctx.bot, ctx.channel_id, voice_channel_name)
    await session.panel.post(session, reply_to_id=ctx.message.get("id"))


async def run_watch(ctx, query):
    if not ctx.channel_id:
        return
    voice_channel_id = await ctx.bot.voice.find_member(ctx.author.id)
    if voice_channel_id is None:
        await ctx.reply("join a voice channel first, then run `!watch` again.")
        return
    if await _refuse_if_busy(ctx, voice_channel_id):
        return
    try:
        item = await _find_item(ctx, query)
    except jellyfin_core.JellyfinAuthError:
        await ctx.reply("jellyfin is unavailable right now.")
        return
    if item is not None:
        await begin(ctx, item)
