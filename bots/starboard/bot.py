#!/usr/bin/env python3
"""bot-starboard: reposts well-reacted messages to a highlights channel and posts a weekly digest; see README.md."""

import asyncio
import os
import time
import uuid

from slimbots import Bot


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS seen (
            message_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            content TEXT NOT NULL,
            attachments INTEGER NOT NULL DEFAULT 0,
            seen_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS starred (
            message_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            author_id TEXT NOT NULL,
            content TEXT NOT NULL,
            attachments INTEGER NOT NULL DEFAULT 0,
            highlight_id TEXT NOT NULL,
            count INTEGER NOT NULL,
            starred_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.commit()


bot = Bot(prefix="!", require_channels=True, default_data_path="starboard.db", store_migrate=init_db)

STARBOARD_CHANNEL = bot.setting("STARBOARD_CHANNEL", required=True)
EMOJI = bot.setting("STARBOARD_EMOJI", "⭐")
THRESHOLD = bot.setting("STARBOARD_THRESHOLD", 3, type=int)
DIGEST_DAYS = bot.setting("STARBOARD_DIGEST_DAYS", 7, type=int)
DIGEST_TOP = bot.setting("STARBOARD_DIGEST_TOP", 5, type=int)
LINK_TEMPLATE = bot.setting("STARBOARD_LINK_TEMPLATE", None)
SEEN_RETENTION_DAYS = bot.setting("STARBOARD_SEEN_RETENTION_DAYS", 14, type=int)

MAX_QUOTE_LEN = 1500
DIGEST_SNIPPET_LEN = 80
MAINTENANCE_SECONDS = 3600
DAY = 86400


def normalize(emoji):
    """Drops the variation selector, so a client that appends one still matches the configured emoji."""
    return emoji.replace("️", "")


def count_for(reactions):
    return sum(r["count"] for r in reactions if normalize(r["emoji"]) == normalize(EMOJI))


def highlight_id(message_id):
    """Derived from the original, so a retry after a crash reposts under the same id and the server drops the duplicate."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"slimm-starboard:{message_id}"))


def digest_id(period_start):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"slimm-starboard-digest:{period_start}"))


def link_for(channel_id, message_id):
    """The channel route only: the client has no per-message anchor to link to yet."""
    template = LINK_TEMPLATE or (os.environ["SLIMM_URL"].rstrip("/") + "/channels/{channel_id}" if os.environ.get("SLIMM_URL") else None)
    return template.format(channel_id=channel_id, message_id=message_id) if template else None


def channel_label(channel_id):
    channel = bot.space.channels.get(channel_id) if bot.space else None
    return f"#{channel.name}" if channel else "another channel"


def author_label(author_id):
    member = bot.space.members.get(author_id) if bot.space else None
    return member.display_name if member else author_id


def snippet(text, limit):
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def render_highlight(message_id, channel_id, author_id, content, attachments, count):
    """One highlight's whole body; an edit only carries `content`, so the count and the quote live together."""
    header = f"{EMOJI} {count} - {author_label(author_id)} in {channel_label(channel_id)}"
    link = link_for(channel_id, message_id)
    if link:
        header += f" - {link}"
    quote = "\n".join(f"> {line}" for line in content[:MAX_QUOTE_LEN].splitlines()) if content else ""
    extra = f"(+{attachments} attachment{'s' if attachments != 1 else ''})" if attachments else ""
    return "\n".join(part for part in (header, quote, extra) if part)


# --- durable state ---


def remember_message(conn, message_id, channel_id, author_id, content, attachments):
    conn.execute(
        "INSERT INTO seen (message_id, channel_id, author_id, content, attachments, seen_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(message_id) DO UPDATE SET content = excluded.content, attachments = excluded.attachments",
        (message_id, channel_id, author_id, content, attachments, int(time.time())),
    )
    conn.commit()


def update_content(conn, message_id, content):
    conn.execute("UPDATE seen SET content = ? WHERE message_id = ?", (content, message_id))
    conn.execute("UPDATE starred SET content = ? WHERE message_id = ?", (content, message_id))
    conn.commit()


def seen_row(conn, message_id):
    return conn.execute(
        "SELECT message_id, channel_id, author_id, content, attachments FROM seen WHERE message_id = ?", (message_id,)
    ).fetchone()


def starred_row(conn, message_id):
    return conn.execute(
        "SELECT message_id, channel_id, author_id, content, attachments, highlight_id, count "
        "FROM starred WHERE message_id = ?",
        (message_id,),
    ).fetchone()


def add_starred(conn, seen, hl_id, count):
    conn.execute(
        "INSERT OR IGNORE INTO starred (message_id, channel_id, author_id, content, attachments, highlight_id, count, starred_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (*seen, hl_id, count, int(time.time())),
    )
    conn.commit()


def set_count(conn, message_id, count):
    conn.execute("UPDATE starred SET count = ? WHERE message_id = ?", (count, message_id))
    conn.commit()


def forget_message(conn, message_id):
    conn.execute("DELETE FROM seen WHERE message_id = ?", (message_id,))
    conn.execute("DELETE FROM starred WHERE message_id = ?", (message_id,))
    conn.commit()


def prune_seen(conn, cutoff):
    """Only the `seen` cache ages out; a starred row is the digest's record and stays."""
    conn.execute("DELETE FROM seen WHERE seen_at < ?", (cutoff,))
    conn.commit()


def top_starred(conn, since, limit):
    return conn.execute(
        "SELECT message_id, channel_id, author_id, content, count FROM starred WHERE starred_at >= ? "
        "ORDER BY count DESC, starred_at ASC LIMIT ?",
        (since, limit),
    ).fetchall()


def meta_get(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return int(row[0]) if row else None


def meta_set(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value))
    )
    conn.commit()


# --- events ---

_locks = {}


def _lock_for(message_id):
    return _locks.setdefault(message_id, asyncio.Lock())


def is_source(channel_id):
    """The highlights channel is never a source, or a highlight could be starred into its own highlight."""
    return channel_id != STARBOARD_CHANNEL


@bot.event
async def on_raw_message(message):
    channel_id = message.get("channel_id")
    if not is_source(channel_id) or not message.get("id") or not message.get("author_id"):
        return
    await bot.store.run(
        remember_message, message["id"], channel_id, message["author_id"],
        message.get("content") or "", len(message.get("attachments") or []),
    )


@bot.event
async def on_reactions_changed(event):
    if not is_source(event.channel_id):
        return
    count = count_for(event.reactions)
    async with _lock_for(event.message_id):
        existing = await bot.store.run(starred_row, event.message_id)
        if existing is not None:
            await refresh_highlight(existing, count)
        elif count >= THRESHOLD:
            await post_highlight(event.message_id, count)


async def post_highlight(message_id, count):
    seen = await bot.store.run(seen_row, message_id)
    if seen is None:
        print(f"starboard: {message_id} reached {count} but was never seen live, so it cannot be mirrored", flush=True)
        return
    hl_id = highlight_id(message_id)
    await bot.client.send(STARBOARD_CHANNEL, render_highlight(*seen, count), message_id=hl_id)
    await bot.store.run(add_starred, seen, hl_id, count)


async def refresh_highlight(existing, count):
    message_id, channel_id, author_id, content, attachments, hl_id, old_count = existing
    if count == old_count:
        return
    await bot.client.edit_message(STARBOARD_CHANNEL, hl_id, render_highlight(message_id, channel_id, author_id, content, attachments, count))
    await bot.store.run(set_count, message_id, count)


@bot.event
async def on_message_edited(event):
    if not is_source(event.channel_id):
        return
    message_id = event.message["id"]
    async with _lock_for(message_id):
        await bot.store.run(update_content, message_id, event.message.get("content") or "")
        existing = await bot.store.run(starred_row, message_id)
        if existing is not None:
            hl_id = existing[5]
            await bot.client.edit_message(STARBOARD_CHANNEL, hl_id, render_highlight(*existing[:5], existing[6]))


@bot.event
async def on_message_deleted(event):
    if not is_source(event.channel_id):
        return
    async with _lock_for(event.message_id):
        existing = await bot.store.run(starred_row, event.message_id)
        await bot.store.run(forget_message, event.message_id)
        if existing is not None:
            await bot.client.delete_message(STARBOARD_CHANNEL, existing[5])


# --- weekly digest ---


def render_digest(rows, days):
    lines = [f"Top highlights from the last {days} day{'s' if days != 1 else ''}:"]
    for i, (message_id, channel_id, author_id, content, count) in enumerate(rows, 1):
        text = snippet(content, DIGEST_SNIPPET_LEN) or "(no text)"
        line = f"{i}. {EMOJI} {count} - {author_label(author_id)} in {channel_label(channel_id)}: {text}"
        link = link_for(channel_id, message_id)
        lines.append(f"{line} - {link}" if link else line)
    return "\n".join(lines)


async def post_digest_if_due(now):
    """The first run only starts the clock; a digest never fires on a fresh install."""
    if DIGEST_DAYS <= 0:
        return
    last = await bot.store.run(meta_get, "last_digest")
    if last is None:
        await bot.store.run(meta_set, "last_digest", now)
        return
    if now - last < DIGEST_DAYS * DAY:
        return
    rows = await bot.store.run(top_starred, last, DIGEST_TOP)
    if rows:
        await bot.client.send(STARBOARD_CHANNEL, render_digest(rows, DIGEST_DAYS), message_id=digest_id(last))
    await bot.store.run(meta_set, "last_digest", now)


async def _maintenance():
    while True:
        now = int(time.time())
        await bot.store.run(prune_seen, now - SEEN_RETENTION_DAYS * DAY)
        await post_digest_if_due(now)
        await asyncio.sleep(MAINTENANCE_SECONDS)


_background_started = False


@bot.event
async def on_ready():
    global _background_started
    if _background_started:
        return
    _background_started = True
    bot.background(_maintenance(), name="starboard-maintenance")


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
