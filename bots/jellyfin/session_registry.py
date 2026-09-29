"""The live watch parties, one per voice channel the bot is streaming in, and how a command finds the right one."""

from __future__ import annotations

_sessions = {}


def add(session):
    _sessions[session.voice_channel_id] = session


def clear():
    _sessions.clear()


def live_sessions():
    """Channel id -> session for every party still running; a finished one is dropped on the way past."""
    for channel_id in [cid for cid, session in _sessions.items() if session.finished]:
        del _sessions[channel_id]
    return dict(_sessions)


def session_for_channel(channel_id):
    return live_sessions().get(channel_id)


def session_for_panel(message_id):
    """The live party whose now-playing panel is this message, or None once it ended."""
    for session in live_sessions().values():
        if session.panel is not None and session.panel.message_id == message_id:
            return session
    return None


def channel_name(bot, channel_id):
    channel = bot.space.channels.get(channel_id)
    return f"#{channel.name}" if channel is not None else channel_id


def _names(bot, channel_ids):
    return ", ".join(channel_name(bot, channel_id) for channel_id in channel_ids)


async def resolve_session(ctx):
    """The party in the voice channel the invoker is in; replies and returns None when there isn't one.
    A person in no call gets the only party if exactly one runs, so `!np` still works from a phone."""
    live = live_sessions()
    if not live:
        await ctx.reply("nothing is playing.")
        return None
    channel_id = await ctx.bot.voice.find_member(ctx.author.id)
    if channel_id in live:
        return live[channel_id]
    if channel_id is None and len(live) == 1:
        return next(iter(live.values()))
    running = _names(ctx.bot, live)
    if channel_id is None:
        await ctx.reply(f"streams are playing in {running} - join the voice channel you mean, then try again.")
    else:
        await ctx.reply(f"nothing is playing in {channel_name(ctx.bot, channel_id)} (streaming in {running}).")
    return None
