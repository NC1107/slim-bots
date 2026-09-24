#!/usr/bin/env python3
"""A slim-m bot that sets reminders: `!remind me in 2h <text>`, `!remind me
at 15:30 <text>`, `!remind me every monday [at 09:00] <text>`, `!remind me
every 2h <text>`, and `!reminders` to list, cancel, edit or snooze your own.
`!timezone <IANA name>` sets the zone `at`/`every ... at` and the listing are
shown in.

Run it with a bot token from Space settings -> Bots, and the ids of the
channels it should watch:

    pip install -r requirements.txt
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_CHANNELS=<channel-uuid>,<channel-uuid> python3 bot.py

`bot-ping/` proves a bot can connect and answer, and says so in its
own docstring while listing three corners it deliberately cuts: no cursor, a
flat reconnect delay, and it answers everywhere it can see. This bot turns
all three, using the `slimbots` package (`../slimbots/`) for the parts that
are the same in every template that needs them - auth, the REST call, the
websocket handshake, and the reconnect loop:

1. **A cursor.** Every processed message's `seq` is written to sqlite before
   the next one is read. On reconnect, `/sync` replays anything sent while
   the socket was down, so a `!remind` typed during an outage still lands.
   On first run per channel there is nothing to catch up on, so the bot
   baselines at the channel's current head instead of replaying its whole
   history - otherwise every bot's first minute would be spent re-triggering
   years of old `!remind` messages.
2. **Exponential backoff**, 1s doubling to a 60s cap, reset once a connection
   actually completes its hello handshake rather than on every attempt.
3. **Channel scoping.** `SLIMM_CHANNELS` is the explicit list of channels
   this bot answers in; everywhere else it stays silent even if it can see
   the traffic.

**Recurring reminders.** A row is never re-created on fire: `due_at` is
recomputed in place and the row kept `sent = 0`, which is what lets
`!reminders`/`cancel`/`edit`/`snooze` keep working on a recurring reminder
exactly like a one-off. Because the same row can fire more than once, the
delivered message's idempotency key can no longer be the row's own id (that
was safe only because a one-off row fires exactly once) - it is instead
derived from `(reminder_id, due_at)`, so a crash-and-retry of the *same*
firing still cannot double-post, while the *next* occurrence gets a fresh
id. See `recurrence.py` for the weekly/interval scheduling math.

**Guarding against the misfire this repo actually hit.** A reminder whose
text is `!daily` used to make bot-casino credit the *reminders bot's own
account* when it fired, because casino keyed the balance on the message
author without checking whether that author was a bot. `slimbots.AuthorFilter`
fixes that at the source (every template ignores bot/webhook authors by
default now), and this bot adds a second, independent layer: delivered
reminder text is always wrapped in a code span, so even a bot with its own,
more permissive matching would see literal backtick-quoted text rather than
something that starts with `!`.

Durable state lives in a sqlite file next to the script (`SLIMM_DB_PATH`,
default `reminders.db`).

What this deliberately leaves out:

- **A precise catch-up across a `/sync` reset.** If the bot is offline long
  enough that a channel's cursor falls outside what `/sync` can answer, the
  response says `reset: true` and this bot re-baselines at the channel's
  current head rather than trying to reconstruct exactly what it missed -
  the same "eventually consistent, resynchronize forward" tradeoff slim-m's
  own reactions and pins accept (decision 0009). A `!remind` typed inside
  that specific gap is the one case this bot can silently miss.
- **Sub-minute recurrence, or recurring more than once a day by weekday.**
  `every <weekday>` fires once a week by design; a tighter cadence wants
  `every <duration>` instead, bounded below by `MIN_RECUR_SECONDS` so a
  typo like `every 1s` cannot turn into a reminder storm.
- **A leap-second-exact weekly fire across a DST transition.** `recurrence`
  computes the next occurrence in the reminder's own stored timezone via
  `zoneinfo`, which handles the one-hour jump correctly; it does not attempt
  to be exact to the second through it, and no reminder bot needs to be.
"""

import asyncio
import os
import re
import sqlite3
import sys
import time
import uuid

from slimbots import AuthorFilter, Client, Connection, cursor, run_forever
from slimbots.lifecycle import guard_handler, run_with_shutdown
from slimbots.limits import RateLimiter, ValidationError, require_len, require_range

import recurrence

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNELS = {c for c in os.environ.get("SLIMM_CHANNELS", "").split(",") if c}
DB_PATH = os.environ.get("SLIMM_DB_PATH", "reminders.db")
DUE_CHECK_SECONDS = 5
USER_AGENT = "slimm-bot-reminders/1.0"

# A burst allowance against a script, not a play-speed cap - see bot-casino's own constant of the same shape.
COMMANDS_PER_WINDOW = 12
COMMAND_WINDOW_SECONDS = 10
# How many reminders one person may have pending at once, per channel - the guard against a reminder storm.
MAX_PENDING_PER_USER = 25
# No recurring reminder may fire more often than this - the guard against a one-second recurrence.
MIN_RECUR_SECONDS = 300
MAX_TEXT_LEN = 500
# A resolved (sent or cancelled) reminder is pruned once it is this old; a pending one never is.
REMINDER_RETENTION_SECONDS = 30 * 24 * 3600
PRUNE_INTERVAL_SECONDS = 3600
DEFAULT_RECUR_HOUR = 9

TRIGGER_IN = re.compile(r"^!remind\s+me\s+in\s+(\S+)\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_AT = re.compile(r"^!remind\s+me\s+at\s+(\d{1,2}:\d{2})\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_EVERY = re.compile(
    r"^!remind\s+me\s+every\s+(\S+)(?:\s+at\s+(\d{1,2}:\d{2}))?\s+(\S[\s\S]*)$", re.IGNORECASE
)
TRIGGER_LIST = re.compile(r"^!reminders\s*$", re.IGNORECASE)
TRIGGER_CANCEL = re.compile(r"^!reminders\s+cancel\s+(\d+)\s*$", re.IGNORECASE)
TRIGGER_EDIT = re.compile(r"^!reminders\s+edit\s+(\d+)\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_SNOOZE = re.compile(r"^!reminders\s+snooze\s+(\d+)\s+(\S+)\s*$", re.IGNORECASE)
TRIGGER_TZ_SET = re.compile(r"^!timezone\s+(\S+)\s*$", re.IGNORECASE)
TRIGGER_TZ_SHOW = re.compile(r"^!timezone\s*$", re.IGNORECASE)
DURATION_PART = re.compile(r"(\d{1,6})([smhd])")
DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

_command_limiter = RateLimiter(COMMANDS_PER_WINDOW, COMMAND_WINDOW_SECONDS)


# --- durable state -----------------------------------------------------


def _ensure_column(conn, table, column, declaration):
    """Adds `column` to `table` if an older database file does not have it
    yet - a light, manual migration, so an existing deployment's pending
    reminders survive picking up recurrence and timezones rather than
    needing a fresh database file."""
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
    cursor.init_table(conn)


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
            reminder_id,
            channel_id,
            user_id,
            request_message_id,
            due_at,
            text,
            int(time.time()),
            recur.get("kind"),
            recur.get("interval_seconds"),
            recur.get("weekday"),
            recur.get("hour"),
            recur.get("minute"),
            recur.get("tz"),
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
    """Advances a recurring reminder's own row to its next occurrence,
    instead of marking it sent - see the module docstring."""
    conn.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (next_due_at, reminder_id))
    conn.commit()


def prune_old_reminders(conn, cutoff):
    conn.execute(
        "DELETE FROM reminders WHERE (sent = 1 OR cancelled = 1) AND created_at < ?", (cutoff,)
    )
    conn.commit()


def pending_for_user(conn, channel_id, user_id):
    return conn.execute(
        "SELECT id, due_at, text, recur_kind, recur_interval_seconds, recur_weekday, recur_hour, recur_minute, recur_tz "
        "FROM reminders WHERE channel_id = ? AND user_id = ? AND sent = 0 AND cancelled = 0 "
        "ORDER BY due_at ASC",
        (channel_id, user_id),
    ).fetchall()


def _nth_id(conn, channel_id, user_id, n):
    """1-based, ordered the same way `!reminders` lists them. Returns the
    row id, or `None` if `n` is out of range."""
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


# --- parsing -------------------------------------------------------------


def parse_duration(spec):
    """`2h`, `90s`, `1h30m` -> seconds, or None if `spec` is not one."""
    parts = DURATION_PART.findall(spec)
    if not parts or "".join(f"{n}{u}" for n, u in parts) != spec:
        return None
    return sum(int(n) * DURATION_SECONDS[u] for n, u in parts)


def parse_every(content):
    """`!remind me every <spec> [at HH:MM] <text>` -> a recurrence dict
    (without `tz`, added by the caller once the author's own is known) plus
    the reminder text, or `None` if `content` is not this shape at all.

    `<spec>` is either a weekday name/abbreviation (`monday`, `every mon`) -
    a `weekly` recurrence, defaulting to `DEFAULT_RECUR_HOUR:00` if no
    `at HH:MM` was given - or a duration (`2h`, `90m`) - an `interval`
    recurrence. `at HH:MM` only makes sense paired with a weekday.
    """
    match = TRIGGER_EVERY.match(content)
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
    """Wraps a reminder's own text in a code span before it is ever sent
    back into the channel. This is a formatting choice - it reads as a
    literal quoted note - and a safety one: even a bot with looser matching
    than `slimbots.AuthorFilter` would see backtick-quoted text, never
    something that starts with `!`. Any backtick already in the text is
    replaced so it cannot break out of the span early."""
    escaped = text.replace("`", "'")
    return f"`{escaped}`"


def delivery_id(reminder_id, due_at):
    """The idempotency key for one *firing* of `reminder_id`, not the
    reminder itself - a recurring reminder's row can fire many times, and
    each firing needs its own id, while a retry of the *same* firing (the
    process crashed between sending and rescheduling) must still land on
    the id it already used. Deterministic in both, so no state needs to be
    kept just to remember it."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"slimm-reminder:{reminder_id}:{due_at}"))


def format_due(due_at, tz_name):
    return recurrence.format_local(due_at, tz_name)


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


# --- commands --------------------------------------------------------------


def handle_message(client, conn, me, authors, channel_id, message):
    author_id = message.get("author_id")
    if not authors.should_handle(author_id, me):
        return
    content = (message.get("content") or "").strip()
    request_message_id = message.get("id")

    if content.startswith("!"):
        limited = _command_limiter.check(author_id)
        if limited:
            client.send(channel_id, limited, reply_to_id=request_message_id)
            return

    if match := TRIGGER_IN.match(content):
        spec, text = match.groups()
        seconds = parse_duration(spec)
        if seconds is None or seconds <= 0:
            client.send(channel_id, f"not a duration I understand: `{spec}`", reply_to_id=request_message_id)
            return
        due_at = int(time.time()) + seconds
        _create_and_ack(client, conn, channel_id, author_id, request_message_id, due_at, text)
        return

    if match := TRIGGER_AT.match(content):
        spec, text = match.groups()
        tz_name = get_timezone(conn, author_id)
        hour, minute = (int(part) for part in spec.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            client.send(channel_id, f"not a time I understand: `{spec}`", reply_to_id=request_message_id)
            return
        due_at = recurrence.local_clock_time(int(time.time()), hour, minute, tz_name)
        _create_and_ack(client, conn, channel_id, author_id, request_message_id, due_at, text)
        return

    if parsed := parse_every(content):
        recur, text = parsed
        _handle_every(client, conn, channel_id, author_id, request_message_id, recur, text)
        return

    if TRIGGER_LIST.match(content):
        _reply_with_pending(client, conn, channel_id, author_id, request_message_id)
        return

    if match := TRIGGER_CANCEL.match(content):
        _reply_with_cancel(client, conn, channel_id, author_id, request_message_id, int(match.group(1)))
        return

    if match := TRIGGER_EDIT.match(content):
        _reply_with_edit(client, conn, channel_id, author_id, request_message_id, int(match.group(1)), match.group(2))
        return

    if match := TRIGGER_SNOOZE.match(content):
        _reply_with_snooze(client, conn, channel_id, author_id, request_message_id, int(match.group(1)), match.group(2))
        return

    if match := TRIGGER_TZ_SET.match(content):
        _reply_with_set_timezone(client, conn, channel_id, author_id, request_message_id, match.group(1))
        return

    if TRIGGER_TZ_SHOW.match(content):
        tz_name = get_timezone(conn, author_id)
        client.send(channel_id, f"your timezone is `{tz_name}`", reply_to_id=request_message_id)


def _reply_with_pending(client, conn, channel_id, author_id, request_message_id):
    """Answers `!reminders` with this member's own pending list, or says there is none."""
    rows = pending_for_user(conn, channel_id, author_id)
    if not rows:
        client.send(channel_id, "you have no pending reminders here", reply_to_id=request_message_id)
        return
    tz_name = get_timezone(conn, author_id)
    lines = []
    for i, (_, due_at, text, recur_kind, interval_seconds, weekday, hour, minute, _tz) in enumerate(rows, 1):
        recur_note = format_recurrence(recur_kind, interval_seconds, weekday, hour, minute)
        lines.append(f"{i}. {format_due(due_at, tz_name)}{recur_note} - {text}")
    client.send(channel_id, "\n".join(lines), reply_to_id=request_message_id)


def _reply_with_cancel(client, conn, channel_id, author_id, request_message_id, n):
    ok = cancel_nth(conn, channel_id, author_id, n)
    reply = f"cancelled reminder {n}" if ok else f"no reminder {n}"
    client.send(channel_id, reply, reply_to_id=request_message_id)


def _reply_with_edit(client, conn, channel_id, author_id, request_message_id, n, new_text):
    try:
        new_text = require_len(new_text, max_len=MAX_TEXT_LEN, field="a reminder")
    except ValidationError as err:
        client.send(channel_id, str(err), reply_to_id=request_message_id)
        return
    ok = edit_nth_text(conn, channel_id, author_id, n, new_text)
    reply = f"updated reminder {n}" if ok else f"no reminder {n}"
    client.send(channel_id, reply, reply_to_id=request_message_id)


def _reply_with_snooze(client, conn, channel_id, author_id, request_message_id, n, spec):
    seconds = parse_duration(spec)
    if seconds is None or seconds <= 0:
        client.send(channel_id, f"not a duration I understand: `{spec}`", reply_to_id=request_message_id)
        return
    new_due = snooze_nth(conn, channel_id, author_id, n, seconds)
    if new_due is None:
        client.send(channel_id, f"no reminder {n}", reply_to_id=request_message_id)
        return
    tz_name = get_timezone(conn, author_id)
    client.send(channel_id, f"reminder {n} pushed to {format_due(new_due, tz_name)}", reply_to_id=request_message_id)


def _reply_with_set_timezone(client, conn, channel_id, author_id, request_message_id, tz_name):
    if not recurrence.is_valid_timezone(tz_name):
        client.send(
            channel_id,
            f"`{tz_name}` isn't a timezone I recognise - use an IANA name like `America/New_York`",
            reply_to_id=request_message_id,
        )
        return
    set_timezone(conn, author_id, tz_name)
    client.send(channel_id, f"timezone set to `{tz_name}`", reply_to_id=request_message_id)


def _handle_every(client, conn, channel_id, author_id, request_message_id, recur, text):
    if recur["kind"] == "interval":
        try:
            require_range(recur["interval_seconds"], min_value=MIN_RECUR_SECONDS, field="a recurring interval")
        except ValidationError as err:
            client.send(channel_id, str(err), reply_to_id=request_message_id)
            return
    recur["tz"] = get_timezone(conn, author_id)
    if recur["kind"] == "weekly":
        due_at = recurrence.next_weekly(int(time.time()), recur["weekday"], recur["hour"], recur["minute"], recur["tz"])
    else:
        due_at = int(time.time()) + recur["interval_seconds"]
    _create_and_ack(client, conn, channel_id, author_id, request_message_id, due_at, text, recur=recur)


def _create_and_ack(client, conn, channel_id, author_id, request_message_id, due_at, text, recur=None):
    if pending_count(conn, channel_id, author_id) >= MAX_PENDING_PER_USER:
        client.send(
            channel_id,
            f"you already have {MAX_PENDING_PER_USER} reminders pending here - cancel one first",
            reply_to_id=request_message_id,
        )
        return
    try:
        text = require_len(text, max_len=MAX_TEXT_LEN, field="a reminder")
    except ValidationError as err:
        client.send(channel_id, str(err), reply_to_id=request_message_id)
        return
    reminder_id = str(uuid.uuid4())
    add_reminder(conn, reminder_id, channel_id, author_id, request_message_id, due_at, text, recur=recur)
    tz_name = (recur or {}).get("tz") or get_timezone(conn, author_id)
    recur_note = format_recurrence(
        (recur or {}).get("kind"),
        (recur or {}).get("interval_seconds"),
        (recur or {}).get("weekday"),
        (recur or {}).get("hour"),
        (recur or {}).get("minute"),
    )
    client.send(channel_id, f"will remind you at {format_due(due_at, tz_name)}{recur_note}", reply_to_id=request_message_id)


async def due_checker(client, conn):
    while True:
        await asyncio.sleep(DUE_CHECK_SECONDS)
        for row in due_reminders(conn, int(time.time())):
            (reminder_id, channel_id, request_message_id, text, due_at,
             recur_kind, interval_seconds, weekday, hour, minute, tz_name) = row
            client.send(
                channel_id,
                f"reminder: {render_reminder_text(text)}",
                message_id=delivery_id(reminder_id, due_at),
                reply_to_id=request_message_id,
            )
            now = int(time.time())
            if recur_kind == "interval":
                reschedule(conn, reminder_id, recurrence.next_interval(due_at, interval_seconds, now))
            elif recur_kind == "weekly":
                reschedule(conn, reminder_id, recurrence.next_weekly(due_at, weekday, hour, minute, tz_name))
            else:
                mark_sent(conn, reminder_id)


async def maintenance(conn):
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        prune_old_reminders(conn, int(time.time()) - REMINDER_RETENTION_SECONDS)


def resync(client, conn, authors):
    """Catches up every scoped channel over `/sync` before the socket opens,
    so a `!remind` sent while the previous session was offline is not lost."""
    scopes = [{"channel_id": c, "after_seq": cursor.get(conn, c)} for c in CHANNELS]
    me = client.me()["id"]
    for scope in cursor.sync(client, scopes):
        channel_id = scope["channel_id"]
        for message in scope["messages"]:
            guard_handler(handle_message, client, conn, me, authors, channel_id, message)
        if scope["messages"]:
            cursor.set(conn, channel_id, scope["messages"][-1]["seq"])
        elif scope["reset"]:
            cursor.bootstrap(client, conn, channel_id)


async def attempt(client, conn, authors, reset_delay):
    me = client.me()["id"]
    print(f"connected as {me}", flush=True)

    for channel_id in CHANNELS:
        cursor.bootstrap(client, conn, channel_id)
    resync(client, conn, authors)

    async with await Connection.open(client) as socket:
        print("listening", flush=True)
        reset_delay()

        due_task = asyncio.create_task(due_checker(client, conn))
        prune_task = asyncio.create_task(maintenance(conn))
        try:
            async for frame in socket.frames():
                # Ignore a frame type we do not know; see bot-ping's docstring.
                if frame.get("type") != "message.created":
                    continue
                channel_id = frame.get("channel_id")
                if channel_id not in CHANNELS:
                    continue
                message = frame.get("message") or {}
                # One bad message must not tear down the whole connection.
                guard_handler(handle_message, client, conn, me, authors, channel_id, message)
                seq = message.get("seq")
                if seq is not None:
                    cursor.set(conn, channel_id, seq)
        finally:
            due_task.cancel()
            prune_task.cancel()


async def main():
    if not BASE or not TOKEN or not CHANNELS:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN and SLIMM_CHANNELS", file=sys.stderr)
        return 2

    client = Client(BASE, TOKEN, USER_AGENT)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    authors = AuthorFilter(client)

    return await run_forever(lambda reset_delay: attempt(client, conn, authors, reset_delay))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_with_shutdown(main)) or 0)
