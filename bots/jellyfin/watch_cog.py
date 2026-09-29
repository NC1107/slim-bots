"""bot-jellyfin's `!pause`/`!resume`/`!seek`/`!np`/`!stop`/`!subs`/`!quality` commands and the `!watch` entry point; see README.md."""

from __future__ import annotations

from slimbots import Permissions
from slimbots.voice import VoiceError

import controls
import jellyfin_core
import panel
import picker
import quality
import session_registry
import watch_start
from stream_session import StreamError, format_hms, parse_hms


def _may_control(ctx, session):
    return ctx.author.id == session.started_by_id or ctx.author.has_permission(Permissions.MANAGE_CHANNELS)


async def run_pause(ctx):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can pause it.")
        return
    if not session.pause():
        await ctx.reply("already paused.")
        return
    await ctx.reply("paused.")
    await session.refresh_panel()


async def run_resume(ctx):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can resume it.")
        return
    if not session.resume():
        await ctx.reply("already playing.")
        return
    await ctx.reply("resumed.")
    await session.refresh_panel()


async def run_seek(ctx, position_text):
    session = await session_registry.resolve_session(ctx)
    if session is None:
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
    await session.refresh_panel()


async def run_stop(ctx):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can stop it.")
        return
    await session.stop(reason=f"stopped by {ctx.author.display_name}")


async def run_now_playing(ctx):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    await ctx.reply(embed=session.now_playing_embed())


async def run_subs(ctx, language):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await ctx.reply("only the person who started this, or a channel manager, can change subtitles.")
        return
    language = (language or "").strip()
    if not language or language.lower() == "off":
        await session.set_subtitle(None, None)
        await ctx.reply("subtitles off.")
        await session.refresh_panel()
        return
    stream = jellyfin_core.find_subtitle_stream(session.item, language)
    if stream is None:
        await ctx.reply(f'no subtitle track matching "{language}".')
        return
    await session.set_subtitle(stream["Index"], stream.get("DisplayTitle") or stream.get("Language") or language)
    await ctx.reply(f"subtitles set to {session.subtitle_label}.")
    await session.refresh_panel()


async def run_quality(ctx, preset_name):
    session = await session_registry.resolve_session(ctx)
    if session is None:
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
    await session.refresh_panel()


def setup(bot):
    @bot.command(name="watch", help="Start a watch party in your voice channel; with no title, offers the last thing you were watching", usage="[title]")
    async def watch(ctx, query: str = ""):
        await watch_start.run_watch(ctx, query)

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

    bot.button(prefix=panel.ID_PREFIX)(controls.on_panel_press)
    bot.button(prefix=picker.ID_PREFIX)(picker.on_pick_press)
    for control_id, label, icon, action in controls.CALL_CONTROLS:
        bot.call_control(control_id, label, icon=icon)(controls.call_control_handler(action))

    @bot.event
    async def on_voice_activity(event):
        session = session_registry.session_for_channel(event.channel_id)
        if session is not None:
            session.wake_monitor()
