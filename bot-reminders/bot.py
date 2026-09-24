#!/usr/bin/env python3
"""bot-reminders: `!remind me in/at/every ...`, `!reminders`, `!timezone`; see README.md."""

import asyncio
import os
import re
import sqlite3
import time
import uuid

from slimbots import Bot, Embed, RateLimiter
from slimbots.limits import ValidationError, require_len, require_range

import recurrence

DB_PATH = os.environ.get("SLIMM_DB_PATH", "reminders.db")
DUE_CHECK_SECONDS = 5

COMMANDS_PER_WINDOW = 12
COMMAND_WINDOW_SECONDS = 10
MAX_PENDING_PER_USER = 25  # per person, per channel - the guard against a reminder storm
MIN_RECUR_SECONDS = 300  # no recurring reminder may fire more often than this
MAX_TEXT_LEN = 500
REMINDER_RETENTION_SECONDS = 30 * 24 * 3600  # a resolved reminder is pruned once this old; a pending one never is
PRUNE_INTERVAL_SECONDS = 3600
DEFAULT_RECUR_HOUR = 9

TRIGGER_IN = re.compile(r"^me\s+in\s+(\S+)\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_AT = re.compile(r"^me\s+at\s+(\d{1,2}:\d{2})\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_EVERY = re.compile(r"^me\s+every\s+(\S+)(?:\s+at\s+(\d{1,2}:\d{2}))?\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_CANCEL = re.compile(r"^cancel\s+(\d+)\s*$", re.IGNORECASE)
TRIGGER_EDIT = re.compile(r"^edit\s+(\d+)\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_SNOOZE = re.compile(r"^snooze\s+(\d+)\s+(\S+)\s*$", re.IGNORECASE)
DURATION_PART = re.compile(r"(\d{1,6})([smhd])")
DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


# --- durable state ---


def _ensure_column(conn, table, column, declaration):
    """A light manual migration, so an older database file picks up new columns without a fresh file."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            request_message_id TEXT NOT NULL,
            due_at INTEGER NOT NULL,
            text TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0,
            cancelled INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_prefs (
            user_id TEXT PRIMARY KEY,
            timezone TEXT NOT NULL DEFAULT 'UTC'
        );
        """
    )
    for column, declaration in (
        ("recur_kind", "TEXT"),
        ("recur_interval_seconds", "INTEGER"),
        ("recur_weekday", "INTEGER"),
        ("recur_hour", "INTEGER"),
        ("recur_minute", "INTEGER"),
        ("recur_tz", "TEXT"),
    ):
        _ensure_column(conn, "reminders", column, declaration)
    conn.commit()


def get_timezone(conn, user_id):
    row = conn.execute("SELECT timezone FROM user_prefs WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else "UTC"


def set_timezone(conn, user_id, tz_name):
    conn.execute(
        "INSERT INTO user_prefs (user_id, timezone) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET timezone = excluded.timezone",
        (user_id, tz_name),
    )
    conn.commit()


def add_reminder(conn, reminder_id, channel_id, user_id, request_message_id, due_at, text, recur=None):
    recur = recur or {}
    conn.execute(
        "INSERT INTO reminders "
        "(id, channel_id, user_id, request_message_id, due_at, text, created_at, "
        " recur_kind, recur_interval_seconds, recur_weekday, recur_hour, recur_minute, recur_tz) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            reminder_id, channel_id, user_id, request_message_id, due_at, text, int(time.time()),
            recur.get("kind"), recur.get("interval_seconds"), recur.get("weekday"),
            recur.get("hour"), recur.get("minute"), recur.get("tz"),
        ),
    )
    conn.commit()


def pending_count(conn, channel_id, user_id):
    row = conn.execute(
        "SELECT COUNT(*) FROM reminders WHERE channel_id = ? AND user_id = ? AND sent = 0 AND cancelled = 0",
        (channel_id, user_id),
    ).fetchone()
    return row[0]


def due_reminders(conn, now):
    return conn.execute(
        "SELECT id, channel_id, request_message_id, text, due_at, "
        "       recur_kind, recur_interval_seconds, recur_weekday, recur_hour, recur_minute, recur_tz "
        "FROM reminders WHERE sent = 0 AND cancelled = 0 AND due_at <= ?",
        (now,),
    ).fetchall()


def mark_sent(conn, reminder_id):
    conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
    conn.commit()


def reschedule(conn, reminder_id, next_due_at):
    """Advances a recurring reminder's own row instead of marking it sent; see README.md."""
    conn.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (next_due_at, reminder_id))
    conn.commit()


def prune_old_reminders(conn, cutoff):
    conn.execute("DELETE FROM reminders WHERE (sent = 1 OR cancelled = 1) AND created_at < ?", (cutoff,))
    conn.commit()


def pending_for_user(conn, channel_id, user_id):
    return conn.execute(
        "SELECT id, due_at, text, recur_kind, recur_interval_seconds, recur_weekday, recur_hour, recur_minute, recur_tz "
        "FROM reminders WHERE channel_id = ? AND user_id = ? AND sent = 0 AND cancelled = 0 "
        "ORDER BY due_at ASC",
        (channel_id, user_id),
    ).fetchall()


def _nth_id(conn, channel_id, user_id, n):
    """1-based, ordered the same way `!reminders` lists them; None if out of range."""
    rows = pending_for_user(conn, channel_id, user_id)
    if n < 1 or n > len(rows):
        return None
    return rows[n - 1][0]


def cancel_nth(conn, channel_id, user_id, n):
    reminder_id = _nth_id(conn, channel_id, user_id, n)
    if reminder_id is None:
        return False
    conn.execute("UPDATE reminders SET cancelled = 1 WHERE id = ?", (reminder_id,))
    conn.commit()
    return True


def edit_nth_text(conn, channel_id, user_id, n, new_text):
    reminder_id = _nth_id(conn, channel_id, user_id, n)
    if reminder_id is None:
        return False
    conn.execute("UPDATE reminders SET text = ? WHERE id = ?", (new_text, reminder_id))
    conn.commit()
    return True


def snooze_nth(conn, channel_id, user_id, n, extra_seconds):
    reminder_id = _nth_id(conn, channel_id, user_id, n)
    if reminder_id is None:
        return None
    row = conn.execute("SELECT due_at FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    new_due = row[0] + extra_seconds
    conn.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (new_due, reminder_id))
    conn.commit()
    return new_due


# --- parsing ---


def parse_duration(spec):
    """`2h`, `90s`, `1h30m` -> seconds, or None if `spec` is not one."""
    parts = DURATION_PART.findall(spec)
    if not parts or "".join(f"{n}{u}" for n, u in parts) != spec:
        return None
    return sum(int(n) * DURATION_SECONDS[u] for n, u in parts)


def parse_every(rest):
    """`me every <spec> [at HH:MM] <text>` -> a recurrence dict (without `tz`) plus the text, or None."""
    match = TRIGGER_EVERY.match(rest)
    if not match:
        return None
    spec, clock, text = match.groups()
    weekday = recurrence.normalize_weekday(spec)
    if weekday is not None:
        hour, minute = (DEFAULT_RECUR_HOUR, 0)
        if clock:
            hour, minute = (int(part) for part in clock.split(":"))
        return {"kind": "weekly", "weekday": weekday, "hour": hour, "minute": minute}, text
    if clock:
        return None  # "at HH:MM" is only meaningful with a weekday
    seconds = parse_duration(spec)
    if seconds is None:
        return None
    return {"kind": "interval", "interval_seconds": seconds}, text


def render_reminder_text(text):
    """Backtick-quotes delivered text - a second, independent layer against a `!`-looking reminder misfiring another bot."""
    return f"`{text.replace(chr(96), chr(39))}`"


def delivery_id(reminder_id, due_at):
    """The idempotency key for one firing of `reminder_id` - a recurring row can fire many times, each needing its own id."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"slimm-reminder:{reminder_id}:{due_at}"))


def format_recurrence(recur_kind, interval_seconds, weekday, hour, minute):
    if recur_kind == "interval":
        return f" (every {format_duration_short(interval_seconds)})"
    if recur_kind == "weekly":
        name = [k for k, v in recurrence.WEEKDAY_NAMES.items() if v == weekday][0]
        return f" (every {name} at {hour:02d}:{minute:02d})"
    return ""


def format_duration_short(seconds):
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


bot = Bot(prefix="!", require_channels=True, cursor_path=DB_PATH)
_command_limiter = RateLimiter(COMMANDS_PER_WINDOW, COMMAND_WINDOW_SECONDS)


@bot.check
async def rate_limit(ctx):
    """A burst allowance against a script, not a play-speed cap on a person."""
    return _command_limiter.check(ctx.author.id)


async def _create_and_ack(ctx, due_at, text, recur=None):
    conn = bot.db
    if pending_count(conn, ctx.channel_id, ctx.author.id) >= MAX_PENDING_PER_USER:
        await ctx.reply(f"you already have {MAX_PENDING_PER_USER} reminders pending here - cancel one first")
        return
    try:
        text = require_len(text, max_len=MAX_TEXT_LEN, field="a reminder")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    reminder_id = str(uuid.uuid4())
    add_reminder(conn, reminder_id, ctx.channel_id, ctx.author.id, ctx.message["id"], due_at, text, recur=recur)
    tz_name = (recur or {}).get("tz") or get_timezone(conn, ctx.author.id)
    recur = recur or {}
    note = format_recurrence(recur.get("kind"), recur.get("interval_seconds"), recur.get("weekday"), recur.get("hour"), recur.get("minute"))
    await ctx.reply(f"will remind you at {recurrence.format_local(due_at, tz_name)}{note}")


@bot.command(help="`me in <duration> <text>`, `me at <HH:MM> <text>`, or `me every <spec> [at HH:MM] <text>`", usage="me in|at|every ...")
async def remind(ctx, rest: str):
    conn = bot.db
    if match := TRIGGER_IN.match(rest):
        spec, text = match.groups()
        seconds = parse_duration(spec)
        if seconds is None or seconds <= 0:
            await ctx.reply(f"not a duration I understand: `{spec}`")
            return
        await _create_and_ack(ctx, int(time.time()) + seconds, text)
        return

    if match := TRIGGER_AT.match(rest):
        spec, text = match.groups()
        hour, minute = (int(part) for part in spec.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            await ctx.reply(f"not a time I understand: `{spec}`")
            return
        tz_name = get_timezone(conn, ctx.author.id)
        due_at = recurrence.local_clock_time(int(time.time()), hour, minute, tz_name)
        await _create_and_ack(ctx, due_at, text)
        return

    if parsed := parse_every(rest):
        recur, text = parsed
        if recur["kind"] == "interval":
            try:
                require_range(recur["interval_seconds"], min_value=MIN_RECUR_SECONDS, field="a recurring interval")
            except ValidationError as err:
                await ctx.reply(str(err))
                return
        recur["tz"] = get_timezone(conn, ctx.author.id)
        if recur["kind"] == "weekly":
            due_at = recurrence.next_weekly(int(time.time()), recur["weekday"], recur["hour"], recur["minute"], recur["tz"])
        else:
            due_at = int(time.time()) + recur["interval_seconds"]
        await _create_and_ack(ctx, due_at, text, recur=recur)
        return

    await ctx.reply("try `!remind me in 2h <text>`, `!remind me at 15:30 <text>`, or `!remind me every monday <text>`")


@bot.command(name="reminders", help="List, `cancel <n>`, `edit <n> <text>`, or `snooze <n> <duration>`", usage="[cancel|edit|snooze <n> ...]")
async def reminders_cmd(ctx, rest: str = ""):
    conn = bot.db
    rest = rest.strip()

    if not rest:
        rows = pending_for_user(conn, ctx.channel_id, ctx.author.id)
        if not rows:
            await ctx.reply("you have no pending reminders here")
            return
        tz_name = get_timezone(conn, ctx.author.id)
        lines = []
        for i, (_, due_at, text, recur_kind, interval_seconds, weekday, hour, minute, _tz) in enumerate(rows, 1):
            note = format_recurrence(recur_kind, interval_seconds, weekday, hour, minute)
            lines.append(f"{i}. {recurrence.format_local(due_at, tz_name)}{note} - {text}")
        await ctx.reply("\n".join(lines))
        return

    if match := TRIGGER_CANCEL.match(rest):
        n = int(match.group(1))
        ok = cancel_nth(conn, ctx.channel_id, ctx.author.id, n)
        await ctx.reply(f"cancelled reminder {n}" if ok else f"no reminder {n}")
        return

    if match := TRIGGER_EDIT.match(rest):
        n, new_text = int(match.group(1)), match.group(2)
        try:
            new_text = require_len(new_text, max_len=MAX_TEXT_LEN, field="a reminder")
        except ValidationError as err:
            await ctx.reply(str(err))
            return
        ok = edit_nth_text(conn, ctx.channel_id, ctx.author.id, n, new_text)
        await ctx.reply(f"updated reminder {n}" if ok else f"no reminder {n}")
        return

    if match := TRIGGER_SNOOZE.match(rest):
        n, spec = int(match.group(1)), match.group(2)
        seconds = parse_duration(spec)
        if seconds is None or seconds <= 0:
            await ctx.reply(f"not a duration I understand: `{spec}`")
            return
        new_due = snooze_nth(conn, ctx.channel_id, ctx.author.id, n, seconds)
        if new_due is None:
            await ctx.reply(f"no reminder {n}")
            return
        tz_name = get_timezone(conn, ctx.author.id)
        await ctx.reply(f"reminder {n} pushed to {recurrence.format_local(new_due, tz_name)}")
        return

    await ctx.reply("try `!reminders`, `!reminders cancel <n>`, `!reminders edit <n> <text>`, or `!reminders snooze <n> <duration>`")


@bot.command(name="timezone", help="Show or set the timezone `at`/`every ... at` and the listing are shown in", usage="[IANA name]")
async def timezone_cmd(ctx, tz_name: str = None):
    conn = bot.db
    if tz_name is None:
        await ctx.reply(f"your timezone is `{get_timezone(conn, ctx.author.id)}`")
        return
    if not recurrence.is_valid_timezone(tz_name):
        await ctx.reply(f"`{tz_name}` isn't a timezone I recognise - use an IANA name like `America/New_York`")
        return
    set_timezone(conn, ctx.author.id, tz_name)
    await ctx.reply(f"timezone set to `{tz_name}`")


async def due_checker():
    while True:
        await asyncio.sleep(DUE_CHECK_SECONDS)
        for row in due_reminders(bot.db, int(time.time())):
            (reminder_id, channel_id, request_message_id, text, due_at,
             recur_kind, interval_seconds, weekday, hour, minute, tz_name) = row
            embed = Embed(title="Reminder", footer=format_recurrence(recur_kind, interval_seconds, weekday, hour, minute).strip() or None)
            await bot.client.send(
                channel_id, f"reminder: {render_reminder_text(text)}", message_id=delivery_id(reminder_id, due_at),
                reply_to_id=request_message_id, embeds=[embed.to_wire()], fallback_content=f"reminder: {render_reminder_text(text)}",
            )
            now = int(time.time())
            if recur_kind == "interval":
                reschedule(bot.db, reminder_id, recurrence.next_interval(due_at, interval_seconds, now))
            elif recur_kind == "weekly":
                reschedule(bot.db, reminder_id, recurrence.next_weekly(due_at, weekday, hour, minute, tz_name))
            else:
                mark_sent(bot.db, reminder_id)


async def _maintenance():
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        prune_old_reminders(bot.db, int(time.time()) - REMINDER_RETENTION_SECONDS)


_background_started = False


@bot.event
async def on_ready():
    global _background_started
    if _background_started:
        return
    _background_started = True
    asyncio.create_task(due_checker())
    asyncio.create_task(_maintenance())


def main():
    bot.db = sqlite3.connect(DB_PATH)
    init_db(bot.db)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
