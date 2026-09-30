#!/usr/bin/env python3
"""bot-minecraft: bridges one channel and a Minecraft server's chat, joins, leaves, deaths and advancements; see README.md.
Split per docs/framework.md: this is the entry point and the pump loops, mc_core.py and its siblings hold the rest."""

import asyncio
import sys

from slimbots import ApiError, Bot
from slimbots.http import is_token_revoked

import mc_core as core
from flow import Batcher, Bridge, MinuteCap
from logtail import LogTail
from rcon import RconClient, RconError

TICK_SECONDS = 0.5
ROSTER_SYNC_SECONDS = 60
ONLINE_COOLDOWN_SECONDS = 5

bot = Bot(prefix="!", require_channels=True)
core.configure(bot)

tail = None
bridge = None
batcher = None
rcon = None
to_game_cap = None
_background_started = False


def setup_state(clock=None):
    """Builds the per-run state from the settings; called once at start, and again by tests with their own clock."""
    global tail, bridge, batcher, rcon, to_game_cap
    kwargs = {"clock": clock} if clock else {}
    tail = LogTail(core.MC_LOG_PATH)
    batcher = Batcher(core.MC_BATCH_SECONDS, core.MC_MAX_LINES_PER_POST, core.MC_MAX_POSTS_PER_MINUTE, core.MC_QUEUE_MAX, **kwargs)
    bridge = Bridge(core.MC_ANNOUNCE, batcher)
    to_game_cap = MinuteCap(core.MC_TO_GAME_PER_MINUTE, **kwargs)
    rcon = RconClient(core.MC_RCON_HOST, core.MC_RCON_PORT, core.MC_RCON_PASSWORD) if core.rcon_enabled() else None


def bridge_channel():
    """MC_CHANNEL, else the one channel the bot is scoped to; None when that is ambiguous."""
    if core.MC_CHANNEL:
        return core.MC_CHANNEL
    channels = bot.channels or set()
    return next(iter(channels)) if len(channels) == 1 else None


async def pump_once():
    """One pass: new log lines into the batcher, then at most one post out to the channel."""
    lines = await asyncio.to_thread(tail.read_new)
    for line in lines:
        event = core.parse_log_line(line)
        if event is not None:
            bridge.handle(event)
    post = batcher.take()
    channel = bridge_channel()
    if post is None or channel is None:
        return
    try:
        await bot.client.send(channel, post)
    except ApiError as err:
        if is_token_revoked(err):
            raise
        print(f"{type(err).__name__}: {err}, dropped one post", file=sys.stderr)


async def pump_loop():
    while True:
        await pump_once()
        await asyncio.sleep(TICK_SECONDS)


async def sync_roster():
    """Replaces the known-online set with the server's own answer; a failed call leaves it as it was."""
    try:
        reply = await rcon.command("list")
    except RconError as err:
        print(f"{err}, keeping the log-derived roster", file=sys.stderr)
        return
    names = core.parse_player_list(reply)
    if names is None:
        print("rcon `list` gave a reply that is not a player list, keeping the log-derived roster", file=sys.stderr)
        return
    bridge.online = set(names)


async def roster_loop():
    while True:
        await sync_roster()
        await asyncio.sleep(ROSTER_SYNC_SECONDS)


@bot.event
async def on_connect():
    global _background_started
    if _background_started:
        return
    _background_started = True
    bot.background(pump_loop(), name="minecraft-pump")
    if rcon is not None:
        bot.background(roster_loop(), name="minecraft-roster")


async def relay_to_game(message):
    """Sends one member's message into the game; the author name comes from the account, never from the text."""
    if not message.get("content") and not message.get("attachments"):
        return
    member = await bot.space.find_member(message["author_id"])
    if member is None:
        return
    text = message.get("content") or "(sent an attachment)"
    command = core.game_command(member.display_name, text)
    if not core.game_safe(text) or not to_game_cap.allow():
        return
    try:
        await rcon.command(command)
    except RconError as err:
        print(f"{err}, dropped one message for the game", file=sys.stderr)


@bot.event
async def on_message(message):
    if rcon is None or message.get("channel_id") != bridge_channel():
        return
    await relay_to_game(message)


@bot.command(name="online", help="List the players on the Minecraft server", cooldown=ONLINE_COOLDOWN_SECONDS)
async def online_cmd(ctx):
    names = sorted(bridge.online)
    note = "from the log, so players who joined before the bot started may be missing"
    if rcon is not None:
        try:
            listed = core.parse_player_list(await rcon.command("list"))
            if listed is None:
                note = "the server's `list` reply was not readable, so this is from the log"
            else:
                names, note = sorted(listed), ""
        except RconError:
            note = "the server did not answer `list`, so this is from the log"
    body = ", ".join(f"`{name}`" for name in names) if names else "nobody"
    await ctx.reply(f"online ({len(names)}): {body}" + (f" ({note})" if note else ""))


def main():
    problem = core.check_config()
    if problem:
        raise SystemExit(problem)
    setup_state()
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
