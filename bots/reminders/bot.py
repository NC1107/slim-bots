#!/usr/bin/env python3
"""bot-reminders: `!remind in/at/every ...`, `!reminders`, `!timezone`; see README.md."""

import asyncio
import time
import uuid

from slimbots import Bot, Duration, Embed, RateLimiter, TimeOfDay
from slimbots.limits import ValidationError, require_len, require_range
from slimbots.migrations import ensure_columns

import recurrence

DUE_CHECK_SECONDS = 5

COMMANDS_PER_WINDOW = 12
COMMAND_WINDOW_SECONDS = 10
MAX_PENDING_PER_USER = 25  # per person, per channel - the guard against a reminder storm
MIN_RECUR_SECONDS = 300  # no recurring reminder may fire more often than this
MAX_TEXT_LEN = 500
REMINDER_RETENTION_SECONDS = 30 * 24 * 3600  # a resolved reminder is pruned once this old; a pending one never is
PRUNE_INTERVAL_SECONDS = 3600
DEFAULT_RECUR_HOUR = 9


# --- durable state ---


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
    ensure_columns(conn, "reminders", {
        "recur_kind": "TEXT",
        "recur_interval_seconds": "INTEGER",
        "recur_weekday": "INTEGER",
        "recur_hour": "INTEGER",
        "recur_minute": "INTEGER",
        "recur_tz": "TEXT",
    })
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


# --- formatting ---


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


bot = Bot(prefix="!", require_channels=True, default_data_path="reminders.db", store_migrate=init_db)
_command_limiter = RateLimiter(COMMANDS_PER_WINDOW, COMMAND_WINDOW_SECONDS)


@bot.check
async def rate_limit(ctx):
    """A burst allowance against a script, not a play-speed cap on a person."""
    return _command_limiter.check(ctx.author.id)


def _txn_create_reminder(conn, channel_id, user_id, request_message_id, due_at, text, recur):
    """The whole create - cap check, validation, insert - in one store call, so two racing creates can't both slip past the cap."""
    if pending_count(conn, channel_id, user_id) >= MAX_PENDING_PER_USER:
        return f"you already have {MAX_PENDING_PER_USER} reminders pending here - cancel one first"
    try:
        text = require_len(text, max_len=MAX_TEXT_LEN, field="a reminder")
    except ValidationError as err:
        return str(err)
    reminder_id = str(uuid.uuid4())
    add_reminder(conn, reminder_id, channel_id, user_id, request_message_id, due_at, text, recur=recur)
    recur = recur or {}
    tz_name = recur.get("tz") or get_timezone(conn, user_id)
    note = format_recurrence(recur.get("kind"), recur.get("interval_seconds"), recur.get("weekday"), recur.get("hour"), recur.get("minute"))
    return f"will remind you at {recurrence.format_local(due_at, tz_name)}{note}"


async def _create_and_ack(ctx, due_at, text, recur=None):
    message = await bot.store.run(_txn_create_reminder, ctx.channel_id, ctx.author.id, ctx.message["id"], due_at, text, recur)
    await ctx.reply(message)


@bot.group(name="remind", help="`in <duration> <text>`, `at <HH:MM> <text>`, or `every <spec> [at HH:MM] <text>`")
async def remind(ctx, rest: str = ""):
    await ctx.reply("try `!remind in 2h <text>`, `!remind at 15:30 <text>`, or `!remind every monday <text>`")


@remind.command(name="in", help="Remind you after a duration", usage="<duration> <text>")
async def remind_in(ctx, duration: Duration, text: str):
    await _create_and_ack(ctx, int(time.time()) + int(duration), text)


@remind.command(name="at", help="Remind you at a time of day, in your own timezone", usage="<HH:MM> <text>")
async def remind_at(ctx, when: TimeOfDay, text: str):
    tz_name = await bot.store.run(get_timezone, ctx.author.id)
    due_at = recurrence.local_clock_time(int(time.time()), when.hour, when.minute, tz_name)
    await _create_and_ack(ctx, due_at, text)


@remind.command(
    name="every", usage="<duration|weekday> [at <HH:MM>] <text>",
    help="A recurring reminder: a duration, or a weekday with an optional `at HH:MM`",
)
async def remind_every(ctx, spec: str, rest: str):
    """`spec` is a duration or a weekday name; `at HH:MM` inside `rest` only applies to a weekday."""
    weekday = recurrence.normalize_weekday(spec)
    interval_seconds = None
    if weekday is None:
        duration = Duration.parse(spec)
        if duration is None:
            await ctx.reply(f"not a duration or weekday I understand: `{spec}`")
            return
        interval_seconds = int(duration)

    hour, minute, text = DEFAULT_RECUR_HOUR, 0, rest
    first, _, remainder = rest.partition(" ")
    if first.lower() == "at":
        clock_token, _, remaining_text = remainder.partition(" ")
        clock = TimeOfDay.parse(clock_token)
        if clock is None:
            await ctx.reply(f"not a time I understand: `{clock_token}`")
            return
        if weekday is None:
            await ctx.reply("`at HH:MM` only makes sense with a weekday, not a plain interval")
            return
        hour, minute, text = clock.hour, clock.minute, remaining_text

    if not text:
        await ctx.reply("missing a value for `text`")
        return

    if weekday is not None:
        recur = {"kind": "weekly", "weekday": weekday, "hour": hour, "minute": minute}
    else:
        try:
            require_range(interval_seconds, min_value=MIN_RECUR_SECONDS, field="a recurring interval")
        except ValidationError as err:
            await ctx.reply(str(err))
            return
        recur = {"kind": "interval", "interval_seconds": interval_seconds}

    recur["tz"] = await bot.store.run(get_timezone, ctx.author.id)
    if recur["kind"] == "weekly":
        due_at = recurrence.next_weekly(int(time.time()), recur["weekday"], recur["hour"], recur["minute"], recur["tz"])
    else:
        due_at = int(time.time()) + recur["interval_seconds"]
    await _create_and_ack(ctx, due_at, text, recur=recur)


def _txn_list_pending(conn, channel_id, user_id):
    return pending_for_user(conn, channel_id, user_id), get_timezone(conn, user_id)


@bot.group(name="reminders", help="List your pending reminders, or manage one by its listed number")
async def reminders_group(ctx, rest: str = ""):
    rest = rest.strip()
    if not rest:
        rows, tz_name = await bot.store.run(_txn_list_pending, ctx.channel_id, ctx.author.id)
        if not rows:
            await ctx.reply("you have no pending reminders here")
            return
        lines = []
        for i, (_, due_at, text, recur_kind, interval_seconds, weekday, hour, minute, _tz) in enumerate(rows, 1):
            note = format_recurrence(recur_kind, interval_seconds, weekday, hour, minute)
            lines.append(f"{i}. {recurrence.format_local(due_at, tz_name)}{note} - {text}")
        await ctx.reply("\n".join(lines))
        return
    await ctx.reply("try `!reminders`, `!reminders cancel <n>`, `!reminders edit <n> <text>`, or `!reminders snooze <n> <duration>`")


@reminders_group.command(name="cancel", help="Cancel one by its listed number", usage="<n>")
async def reminders_cancel(ctx, n: int):
    ok = await bot.store.run(cancel_nth, ctx.channel_id, ctx.author.id, n)
    await ctx.reply(f"cancelled reminder {n}" if ok else f"no reminder {n}")


@reminders_group.command(name="edit", help="Change one's text by its listed number", usage="<n> <text>")
async def reminders_edit(ctx, n: int, text: str):
    try:
        text = require_len(text, max_len=MAX_TEXT_LEN, field="a reminder")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    ok = await bot.store.run(edit_nth_text, ctx.channel_id, ctx.author.id, n, text)
    await ctx.reply(f"updated reminder {n}" if ok else f"no reminder {n}")


@reminders_group.command(name="snooze", help="Push one back by a duration", usage="<n> <duration>")
async def reminders_snooze(ctx, n: int, duration: Duration):
    new_due = await bot.store.run(snooze_nth, ctx.channel_id, ctx.author.id, n, int(duration))
    if new_due is None:
        await ctx.reply(f"no reminder {n}")
        return
    tz_name = await bot.store.run(get_timezone, ctx.author.id)
    await ctx.reply(f"reminder {n} pushed to {recurrence.format_local(new_due, tz_name)}")


@bot.command(name="timezone", help="Show or set the timezone `at`/`every ... at` and the listing are shown in", usage="[IANA name]")
async def timezone_cmd(ctx, tz_name: str = None):
    if tz_name is None:
        current = await bot.store.run(get_timezone, ctx.author.id)
        await ctx.reply(f"your timezone is `{current}`")
        return
    if not recurrence.is_valid_timezone(tz_name):
        await ctx.reply(f"`{tz_name}` isn't a timezone I recognise - use an IANA name like `America/New_York`")
        return
    await bot.store.run(set_timezone, ctx.author.id, tz_name)
    await ctx.reply(f"timezone set to `{tz_name}`")


async def due_checker():
    while True:
        await asyncio.sleep(DUE_CHECK_SECONDS)
        for row in await bot.store.run(due_reminders, int(time.time())):
            (reminder_id, channel_id, request_message_id, text, due_at,
             recur_kind, interval_seconds, weekday, hour, minute, tz_name) = row
            embed = Embed(title="Reminder", footer=format_recurrence(recur_kind, interval_seconds, weekday, hour, minute).strip() or None)
            await bot.client.send(
                channel_id, f"reminder: {render_reminder_text(text)}", message_id=delivery_id(reminder_id, due_at),
                reply_to_id=request_message_id, embeds=[embed.to_wire()], fallback_content=f"reminder: {render_reminder_text(text)}",
            )
            now = int(time.time())
            if recur_kind == "interval":
                await bot.store.run(reschedule, reminder_id, recurrence.next_interval(due_at, interval_seconds, now))
            elif recur_kind == "weekly":
                await bot.store.run(reschedule, reminder_id, recurrence.next_weekly(due_at, weekday, hour, minute, tz_name))
            else:
                await bot.store.run(mark_sent, reminder_id)


async def _maintenance():
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        await bot.store.run(prune_old_reminders, int(time.time()) - REMINDER_RETENTION_SECONDS)


_background_started = False


@bot.event
async def on_ready():
    global _background_started
    if _background_started:
        return
    _background_started = True
    bot.background(due_checker(), name="reminders-due-checker")
    bot.background(_maintenance(), name="reminders-maintenance")


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
