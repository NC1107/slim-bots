#!/usr/bin/env python3
"""bot-starboard: reposts well-reacted messages to a highlights channel and posts a weekly digest; see README.md."""

import asyncio
import time
import uuid

from slimbots import Bot
from slimbots.http import ApiError
from slimbots.migrations import ensure_columns


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
    for table in ("seen", "starred"):
        ensure_columns(conn, table, {"attachment_ids": "TEXT NOT NULL DEFAULT ''"})


bot = Bot(prefix="!", require_channels=True, default_data_path="starboard.db", store_migrate=init_db)

STARBOARD_CHANNEL = bot.setting("STARBOARD_CHANNEL", required=True)
EMOJI = bot.setting("STARBOARD_EMOJI", "\u2b50")
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
    return emoji.replace("\ufe0f", "")


def count_for(reactions):
    return sum(r["count"] for r in reactions if normalize(r["emoji"]) == normalize(EMOJI))


def highlight_id(message_id):
    """Derived from the original, so a retry after a crash reposts under the same id and the server drops the duplicate."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"slimm-starboard:{message_id}"))


def digest_id(period_start):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"slimm-starboard-digest:{period_start}"))


def link_for(channel_id, message_id):
    """The client's per-message route, which opens the channel scrolled to the message."""
    template = LINK_TEMPLATE or (bot.client.base + "/channels/{channel_id}/m/{message_id}" if bot.client else None)
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


def split_ids(joined):
    return [i for i in joined.split(",") if i]


def render_highlight(message_id, channel_id, author_id, content, attachments, attachment_ids, count):
    """One highlight's whole body; an edit only carries `content`, so the count and the quote live together."""
    header = f"{EMOJI} {count} - {author_label(author_id)} in {channel_label(channel_id)}"
    link = link_for(channel_id, message_id)
    if link:
        header += f" - {link}"
    quote = "\n".join(f"> {line}" for line in content[:MAX_QUOTE_LEN].splitlines()) if content else ""
    left_out = attachments - len(split_ids(attachment_ids))
    plural = "s" if left_out != 1 else ""
    extra = f"(+{left_out} attachment{plural} not shown)" if left_out > 0 else ""
    return "\n".join(part for part in (header, quote, extra) if part)


# --- durable state ---


def remember_message(conn, message_id, channel_id, author_id, content, attachments, attachment_ids):
    conn.execute(
        "INSERT INTO seen (message_id, channel_id, author_id, content, attachments, attachment_ids, seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(message_id) DO UPDATE SET content = excluded.content, "
        "attachments = excluded.attachments, attachment_ids = excluded.attachment_ids",
        (message_id, channel_id, author_id, content, attachments, attachment_ids, int(time.time())),
    )
    conn.commit()


def update_content(conn, message_id, content):
    conn.execute("UPDATE seen SET content = ? WHERE message_id = ?", (content, message_id))
    conn.execute("UPDATE starred SET content = ? WHERE message_id = ?", (content, message_id))
    conn.commit()


def seen_row(conn, message_id):
    return conn.execute(
        "SELECT message_id, channel_id, author_id, content, attachments, attachment_ids FROM seen WHERE message_id = ?", (message_id,)
    ).fetchone()


def starred_row(conn, message_id):
    return conn.execute(
        "SELECT message_id, channel_id, author_id, content, attachments, attachment_ids, highlight_id, count "
        "FROM starred WHERE message_id = ?",
        (message_id,),
    ).fetchone()


def add_starred(conn, seen, hl_id, count):
    conn.execute(
        "INSERT OR IGNORE INTO starred "
        "(message_id, channel_id, author_id, content, attachments, attachment_ids, highlight_id, count, starred_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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


async def is_automated(author_id):
    """A bot's or webhook's own post is never mirrored, and neither is one of this bot's."""
    return author_id == bot.me_id or await bot.authors.is_automated(author_id)


def leaks(origin_id):
    """True unless the origin is known public or the highlights channel is known restricted; the @everyone-wide flag only."""
    channels = bot.space.channels
    origin, target = channels.get(origin_id), channels.get(STARBOARD_CHANNEL)
    origin_public = origin is not None and origin.restricted is False
    target_restricted = target is not None and target.restricted is True
    return not (origin_public or target_restricted)


def attachment_ids_of(attachments):
    return ",".join(a.id for a in attachments)


@bot.event
async def on_raw_message(message):
    channel_id = message.get("channel_id")
    author_id = message.get("author_id")
    if not is_source(channel_id) or not message.get("id") or not author_id or await is_automated(author_id):
        return
    attachments = message.get("attachments") or []
    await bot.store.run(
        remember_message, message["id"], channel_id, author_id, message.get("content") or "",
        len(attachments), ",".join(a["id"] for a in attachments if a.get("id")),
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
            await post_highlight(event.channel_id, event.message_id, count)


async def fetch_unseen(channel_id, message_id):
    """Reads a message this bot never saw live, and remembers it unless an automated account wrote it."""
    try:
        message = await bot.client.get_message(channel_id, message_id)
    except ApiError as err:
        print(f"starboard: {message_id} reached the threshold but could not be fetched: {err}", flush=True)
        return None
    if not message.author_id or await is_automated(message.author_id):
        return None
    await bot.store.run(
        remember_message, message_id, channel_id, message.author_id, message.content or "",
        len(message.attachments), attachment_ids_of(message.attachments),
    )
    return await bot.store.run(seen_row, message_id)


async def send_highlight(seen, hl_id, count):
    """Carries the original's attachments by id; without ATTACH_FILES the highlight goes out as text alone."""
    ids = split_ids(seen[5])
    body = render_highlight(*seen, count)
    try:
        await bot.client.send(STARBOARD_CHANNEL, body, message_id=hl_id, attachment_ids=ids or None)
    except ApiError as err:
        if not ids:
            raise
        print(f"starboard: sending {seen[0]} with its attachments failed ({err}); sending text only", flush=True)
        await bot.client.send(STARBOARD_CHANNEL, body, message_id=hl_id)


async def post_highlight(channel_id, message_id, count):
    if leaks(channel_id):
        print(f"starboard: {message_id} is in a channel that may be restricted, and the highlights channel is not, so it is not mirrored", flush=True)
        return
    seen = await bot.store.run(seen_row, message_id) or await fetch_unseen(channel_id, message_id)
    if seen is None:
        return
    hl_id = highlight_id(message_id)
    await send_highlight(seen, hl_id, count)
    await bot.store.run(add_starred, seen, hl_id, count)


async def refresh_highlight(existing, count):
    *message, hl_id, old_count = existing
    if count == old_count:
        return
    await bot.client.edit_message(STARBOARD_CHANNEL, hl_id, render_highlight(*message, count))
    await bot.store.run(set_count, message[0], count)


@bot.event
async def on_message_edited(event):
    if not is_source(event.channel_id):
        return
    message_id = event.message["id"]
    async with _lock_for(message_id):
        await bot.store.run(update_content, message_id, event.message.get("content") or "")
        existing = await bot.store.run(starred_row, message_id)
        if existing is not None:
            *message, hl_id, count = existing
            await bot.client.edit_message(STARBOARD_CHANNEL, hl_id, render_highlight(*message, count))


@bot.event
async def on_message_deleted(event):
    if not is_source(event.channel_id):
        return
    async with _lock_for(event.message_id):
        existing = await bot.store.run(starred_row, event.message_id)
        await bot.store.run(forget_message, event.message_id)
        if existing is not None:
            await bot.client.delete_message(STARBOARD_CHANNEL, existing[6])


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
