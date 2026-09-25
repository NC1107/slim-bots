#!/usr/bin/env python3
"""bot-starboard: mirrors a message into a highlights channel once its reactions cross a threshold; see README.md."""

import asyncio
import time

from slimbots import ApiError, Bot, Embed
from slimbots.http import is_not_found

# A rolling week from bot start, not a calendar week - see README.md.
DIGEST_INTERVAL_SECONDS = 7 * 24 * 60 * 60
DIGEST_TOP_N = 5
STAR_COLOR = 0xFFD700


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS highlights (
            origin_message_id TEXT PRIMARY KEY,
            origin_channel_id TEXT NOT NULL,
            highlight_message_id TEXT NOT NULL,
            count INTEGER NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()


bot = Bot(prefix="!", require_channels=True, default_data_path="starboard.db", store_migrate=init_db)
STARBOARD_CHANNEL = bot.setting("STARBOARD_CHANNEL")
STARBOARD_EMOJI = bot.setting("STARBOARD_EMOJI", "⭐")
STARBOARD_THRESHOLD = bot.setting("STARBOARD_THRESHOLD", 3, type=int)

starboard_channel = None  # resolved once, in on_connect
_digest_started = False


def resolve_starboard_channel():
    """No single-channel fallback to guess from, unlike `bot-canvas-board`'s `CANVAS_CHANNEL` - there is
    nothing safe to default a highlights channel to."""
    if not STARBOARD_CHANNEL:
        raise RuntimeError("set STARBOARD_CHANNEL to the channel highlights are posted to")
    channel = bot.space.get_channel(STARBOARD_CHANNEL)
    if channel is None:
        raise RuntimeError(f"STARBOARD_CHANNEL={STARBOARD_CHANNEL!r} does not name a channel this bot can see")
    return channel


def leaks_into_starboard(origin, starboard):
    """True if starboard (open to @everyone) would show what origin deliberately hides from @everyone."""
    return origin.restricted and not starboard.restricted


def star_count(reactions):
    return next((r.get("count", 0) for r in reactions if r.get("emoji") == STARBOARD_EMOJI), 0)


def image_attachment_ids(attachments):
    ids = [a["id"] for a in attachments if (a.get("content_type") or "").startswith("image/")]
    return ids or None


def get_highlight(conn, origin_message_id):
    row = conn.execute(
        "SELECT origin_channel_id, highlight_message_id, count FROM highlights WHERE origin_message_id = ?",
        (origin_message_id,),
    ).fetchone()
    return None if row is None else {"origin_channel_id": row[0], "highlight_message_id": row[1], "count": row[2]}


def insert_highlight(conn, origin_message_id, origin_channel_id, highlight_message_id, count):
    conn.execute(
        "INSERT INTO highlights (origin_message_id, origin_channel_id, highlight_message_id, count, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (origin_message_id, origin_channel_id, highlight_message_id, count, int(time.time())),
    )
    conn.commit()


def update_highlight_count(conn, origin_message_id, count):
    conn.execute("UPDATE highlights SET count = ? WHERE origin_message_id = ?", (count, origin_message_id))
    conn.commit()


def update_highlight_message(conn, origin_message_id, highlight_message_id):
    conn.execute(
        "UPDATE highlights SET highlight_message_id = ? WHERE origin_message_id = ?",
        (highlight_message_id, origin_message_id),
    )
    conn.commit()


def delete_highlight(conn, origin_message_id):
    conn.execute("DELETE FROM highlights WHERE origin_message_id = ?", (origin_message_id,))
    conn.commit()


def highlight_content(count):
    return f"{STARBOARD_EMOJI} **{count}**"


def build_embed(author_name, content, origin_channel):
    embed = Embed(description=content or None, color=STAR_COLOR)
    embed.set_author(author_name)
    embed.set_footer(f"#{origin_channel.name}")
    return embed


async def post_highlight(origin_channel, author_name, content, attachments, count):
    """The count rides in `content` (editable) rather than the embed (fixed once sent) - see README.md."""
    embed = build_embed(author_name, content, origin_channel)
    return await bot.client.send(
        starboard_channel.id, highlight_content(count), embeds=[embed.to_wire()],
        attachment_ids=image_attachment_ids(attachments), fallback_content=embed.render_fallback(),
    )


async def forget_if_missing(origin_message_id, err):
    """A highlight message deleted out from under us (by a moderator, say) just means forgetting it here too."""
    if not is_not_found(err):
        raise err
    await bot.store.run(delete_highlight, origin_message_id)


async def create_highlight(channel_id, message_id, count):
    origin_channel = bot.space.get_channel(channel_id)
    if origin_channel is None or leaks_into_starboard(origin_channel, starboard_channel):
        return
    try:
        message = await bot.client.get_message(channel_id, message_id)
    except ApiError as err:
        if is_not_found(err):
            return
        raise
    if message.author_id and await bot.authors.is_automated(message.author_id):
        return
    sent = await post_highlight(
        origin_channel, message.author_display_name or "someone", message.content, message.attachments, count,
    )
    await bot.store.run(insert_highlight, message_id, channel_id, sent.id, count)


@bot.event
async def on_reactions_changed(event):
    if starboard_channel is None:
        return
    count = star_count(event.reactions)
    existing = await bot.store.run(get_highlight, event.message_id)
    if existing is not None:
        if count == existing["count"]:
            return
        await bot.store.run(update_highlight_count, event.message_id, count)
        try:
            await bot.client.edit_message(starboard_channel.id, existing["highlight_message_id"], highlight_content(count))
        except ApiError as err:
            await forget_if_missing(event.message_id, err)
        return
    if count >= STARBOARD_THRESHOLD:
        await create_highlight(event.channel_id, event.message_id, count)


@bot.event
async def on_message_edited(event):
    """Embeds are fixed once sent (no route edits one), so a content change re-sends rather than patches it in place."""
    if starboard_channel is None:
        return
    message = event.message
    existing = await bot.store.run(get_highlight, message.get("id"))
    if existing is None:
        return
    origin_channel = bot.space.get_channel(existing["origin_channel_id"])
    if origin_channel is None:
        return
    try:
        await bot.client.delete_message(starboard_channel.id, existing["highlight_message_id"])
    except ApiError as err:
        if not is_not_found(err):
            raise
    sent = await post_highlight(
        origin_channel, message.get("author_display_name") or "someone", message.get("content"),
        message.get("attachments") or [], existing["count"],
    )
    await bot.store.run(update_highlight_message, message.get("id"), sent.id)


@bot.event
async def on_message_deleted(event):
    if starboard_channel is None:
        return
    existing = await bot.store.run(get_highlight, event.message_id)
    if existing is None:
        return
    try:
        await bot.client.delete_message(starboard_channel.id, existing["highlight_message_id"])
    except ApiError as err:
        if not is_not_found(err):
            raise
    await bot.store.run(delete_highlight, event.message_id)


def top_highlights_since(conn, since_ts):
    return conn.execute(
        "SELECT origin_channel_id, count FROM highlights WHERE created_at >= ? ORDER BY count DESC LIMIT ?",
        (since_ts, DIGEST_TOP_N),
    ).fetchall()


async def post_weekly_digest():
    """The optional weekly roll-up; silent if nothing crossed the threshold in the window."""
    rows = await bot.store.run(top_highlights_since, int(time.time()) - DIGEST_INTERVAL_SECONDS)
    if not rows:
        return
    lines = ["this week's top highlights:"]
    for origin_channel_id, count in rows:
        channel = bot.space.get_channel(origin_channel_id)
        name = channel.name if channel else origin_channel_id
        lines.append(f"{STARBOARD_EMOJI} {count} - from #{name}")
    await bot.client.send(starboard_channel.id, "\n".join(lines))


async def _weekly_digest_loop():
    while True:
        await asyncio.sleep(DIGEST_INTERVAL_SECONDS)
        await post_weekly_digest()


@bot.event
async def on_connect():
    global starboard_channel
    starboard_channel = resolve_starboard_channel()


@bot.event
async def on_ready():
    global _digest_started
    if _digest_started:
        return
    _digest_started = True
    bot.background(_weekly_digest_loop(), name="starboard-weekly-digest")


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
