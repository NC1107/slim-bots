"""bot-music's `play`/`queue`/`skip`/`pause`/`resume`/`stop`/`np` commands; one session per voice channel."""

import asyncio

from slimbots.limits import ValidationError, require_len
from slimbots.voice import VoiceError

import music_core
from player import MusicSession, PlayerError

_sessions = {}
_start_lock = asyncio.Lock()


def active_sessions():
    return _sessions


def _forget(session):
    if _sessions.get(session.voice_channel_id) is session:
        del _sessions[session.voice_channel_id]


def _channel_name(bot, channel_id):
    channel = bot.space.channels.get(channel_id)
    return f"#{channel.name}" if channel is not None else channel_id


async def _resolve_track(ctx, query):
    """The track to play, or None after replying why not."""
    if music_core.is_stream_url(query):
        problem = await asyncio.to_thread(music_core.check_stream_url, query.strip())
        if problem:
            await ctx.reply(problem)
            return None
        return music_core.track_from_url(query.strip())
    if not music_core.jellyfin_enabled():
        await ctx.reply("no music library is configured here - give me a direct `http(s)` stream url.")
        return None
    try:
        items = await asyncio.to_thread(music_core.search_tracks, query)
    except music_core.JellyfinAuthError:
        await ctx.reply("jellyfin is unavailable right now.")
        return None
    if not items:
        await ctx.reply(f'nothing found for "{query}".')
        return None
    return music_core.track_from_item(items[0])


async def _start_session(ctx, voice_channel_id, track):
    """Joins the call and starts playing `track`, or replies why it could not."""
    voice_channel_name = _channel_name(ctx.bot, voice_channel_id)
    try:
        voice_session = await ctx.bot.voice.join(voice_channel_id)
    except VoiceError as err:
        await ctx.reply(f"can't join {voice_channel_name}: {err}")
        return
    if not voice_session.can_publish:
        await voice_session.leave()
        await ctx.reply(f"I can join {voice_channel_name} but can't speak there - I need SPEAK to play audio.")
        return
    session = MusicSession(ctx.bot, voice_channel_id, ctx.channel_id, voice_session, _forget)
    try:
        await session.start(track)
    except (PlayerError, VoiceError) as err:
        await voice_session.leave()
        await ctx.reply(f"could not start playing: {err}")
        return
    _sessions[voice_channel_id] = session
    await ctx.reply(f"playing **{track.title}** in {voice_channel_name}.")


async def run_play(ctx, query):
    if not ctx.channel_id:
        return
    try:
        query = require_len(query.strip(), max_len=music_core.MAX_QUERY_LENGTH, field="a song or url")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    voice_channel_id = await ctx.bot.voice.find_member(ctx.author.id)
    if voice_channel_id is None:
        await ctx.reply("join a voice channel first, then run `play` again.")
        return
    track = await _resolve_track(ctx, query)
    if track is None:
        return
    async with _start_lock:
        session = _sessions.get(voice_channel_id)
        if session is None or session.ended:
            await _start_session(ctx, voice_channel_id, track)
            return
        session.text_channel_id = ctx.channel_id
        position = session.enqueue(track)
    if position is None:
        await ctx.reply(f"the queue is full ({music_core.MAX_QUEUE_LENGTH} tracks).")
        return
    await ctx.reply(f"queued **{track.title}** (#{position} up next).")


async def _invokers_session(ctx):
    """The live session in the call the invoker is in, or None after replying why not."""
    voice_channel_id = await ctx.bot.voice.find_member(ctx.author.id)
    session = _sessions.get(voice_channel_id) if voice_channel_id else None
    if session is None or session.ended:
        await ctx.reply("nothing is playing in your call.")
        return None
    return session


async def run_skip(ctx):
    session = await _invokers_session(ctx)
    if session is None:
        return
    title = session.current.title if session.current else ""
    if session.skip():
        await ctx.reply(f"skipped **{title}**.")
    else:
        await ctx.reply("nothing is playing.")


async def run_pause(ctx):
    session = await _invokers_session(ctx)
    if session is not None:
        await ctx.reply("paused." if session.pause() else "already paused.")


async def run_resume(ctx):
    session = await _invokers_session(ctx)
    if session is not None:
        await ctx.reply("resumed." if session.resume() else "already playing.")


async def run_stop(ctx):
    session = await _invokers_session(ctx)
    if session is not None:
        await session.stop(reason=f"stopped by {ctx.author.display_name}")


async def run_now_playing(ctx):
    session = await _invokers_session(ctx)
    if session is not None:
        await ctx.reply(embed=session.now_playing_embed())


async def run_queue(ctx):
    session = await _invokers_session(ctx)
    if session is None:
        return
    lines = [f"now: **{session.current.title}**"] if session.current else []
    lines += [f"{i}. {track.title}" for i, track in enumerate(list(session.queue)[:10], start=1)]
    hidden = len(session.queue) - 10
    if hidden > 0:
        lines.append(f"...and {hidden} more")
    await ctx.reply("\n".join(lines) if lines else "the queue is empty.")


def setup(bot):
    @bot.command(name="play", help="Play a track from Jellyfin, or a direct stream url, in your voice call", usage="<song|url>")
    async def play(ctx, query: str = ""):
        await run_play(ctx, query)

    @bot.command(name="queue", aliases=("q",), help="Show what is queued in your call")
    async def queue(ctx):
        await run_queue(ctx)

    @bot.command(name="skip", help="Skip the current track")
    async def skip(ctx):
        await run_skip(ctx)

    @bot.command(name="pause", help="Pause playback")
    async def pause(ctx):
        await run_pause(ctx)

    @bot.command(name="resume", help="Resume playback")
    async def resume(ctx):
        await run_resume(ctx)

    @bot.command(name="stop", help="Stop playing, clear the queue and leave the call")
    async def stop(ctx):
        await run_stop(ctx)

    @bot.command(name="np", help="Show the current track")
    async def now_playing(ctx):
        await run_now_playing(ctx)

    @bot.event
    async def on_voice_activity(event):
        session = _sessions.get(event.channel_id)
        if session is not None and not session.ended:
            session.wake_monitor()
