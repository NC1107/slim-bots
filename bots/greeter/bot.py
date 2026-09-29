#!/usr/bin/env python3
"""bot-greeter: posts a configurable welcome message when someone joins; see README.md."""

import sqlite3

from slimbots import Bot, Permissions


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()


bot = Bot(prefix="!", default_data_path="greeter.db", store_migrate=init_db)

GREETER_MESSAGE = bot.setting("GREETER_MESSAGE", "Welcome, {member}! Make yourself at home.")
GREETER_CHANNEL = bot.setting("GREETER_CHANNEL", "general/chat")

CHANNEL_KEY = "welcome_channel"


def welcome_text(member):
    """`{member}` in `GREETER_MESSAGE` becomes an @mention; any other `{...}` is left to a KeyError, logged not fatal."""
    return GREETER_MESSAGE.format(member=member.mention())


def _read_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _write_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


async def _channel_by_path(path):
    """`category/channel`, or a bare `channel`, matched by name case-insensitively; None when nothing matches."""
    category_name, _, channel_name = path.rpartition("/")
    await bot.space.refresh_channels()
    candidates = [c for c in bot.space.channels.values() if c.name.lower() == channel_name.lower()]
    if not category_name:
        return candidates[0].id if candidates else None
    categories = await bot.client.call("GET", "/categories") or []
    wanted = {c["id"] for c in categories if c.get("name", "").lower() == category_name.lower()}
    return next((c.id for c in candidates if c.category_id in wanted), None)


async def welcome_channel():
    """SLIMM_CHANNELS pins it outright; otherwise an admin's `!welcome here`, else `GREETER_CHANNEL`."""
    if bot.channel:
        return bot.channel
    store = await bot.open_store()
    stored = await store.run(_read_setting, CHANNEL_KEY)
    return stored or await _channel_by_path(GREETER_CHANNEL)


@bot.event
async def on_member_join(member):
    channel_id = await welcome_channel()
    if channel_id is None:
        print(f"no welcome channel: nothing matches GREETER_CHANNEL={GREETER_CHANNEL!r}; run !welcome here")
        return
    await bot.client.send(channel_id, welcome_text(member))


@bot.command(name="welcome", help="where welcomes go; `here` moves them to this channel", usage="[here]")
async def welcome(ctx, arg: str = ""):
    if arg != "here":
        channel_id = await welcome_channel()
        channel = bot.space.get_channel(channel_id) if channel_id else None
        await ctx.reply(f"welcomes go to #{channel.name}." if channel else "no welcome channel set - run `!welcome here`.")
        return
    if not ctx.author.has_permission(Permissions.MANAGE_SERVER):
        await ctx.reply_ephemeral("only someone with Manage Server can move the welcome channel.", public_fallback=True)
        return
    if bot.channel:
        await ctx.reply("this bot's SLIMM_CHANNELS pins the welcome channel; change it there instead.")
        return
    store = await bot.open_store()
    await store.run(_write_setting, CHANNEL_KEY, ctx.channel_id)
    await ctx.reply("new members will be welcomed here.")


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
