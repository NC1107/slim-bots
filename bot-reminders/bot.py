#!/usr/bin/env python3
"""A slim-m bot that sets reminders: `!remind me in 2h <text>`, `!remind me
at 15:30 <text>`, and `!reminders` to list or cancel your own.

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

Durable state lives in a sqlite file next to the script (`SLIMM_DB_PATH`,
default `reminders.db`). A reminder row's own id doubles as the eventual
delivery message's idempotency key, so a crash between sending and marking a
reminder `sent` cannot double-post: the next due-check retries the same id
and the server replays what it already stored.

What this deliberately leaves out:

- **Recurring reminders** (`every monday`). Out of scope for this example -
  it needs a schedule grammar and a next-fire-time recompute step that would
  roughly double the file, and the point of an example is to stay readable
  in one sitting. A real deployment wanting this should track a `recur_rule`
  column and recompute `due_at` on send instead of deleting the row.
- **Time zones.** `at HH:MM` is UTC only. A bot that must honor a member's
  local time needs to ask them for one and store it, which is a real feature,
  not a one-line fix.
- **Editing a reminder.** Cancel it and set a new one.
- **Reminders across a restart's downtime gap once `/sync` itself resets.**
  If the bot is offline long enough that a channel's cursor falls outside
  what `/sync` can answer, the response says `reset: true` and this bot
  re-baselines at the channel's current head rather than trying to
  reconstruct exactly what it missed - the same "eventually consistent,
  resynchronize forward" tradeoff slim-m's own reactions and pins accept
  (decision 0009). A `!remind` typed inside that specific gap is the one
  case this bot can silently miss; every ordinary reconnect is covered.
"""

import asyncio
import os
import re
import sqlite3
import sys
import time
import uuid

from slimbots import Client, Connection, cursor, run_forever

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNELS = {c for c in os.environ.get("SLIMM_CHANNELS", "").split(",") if c}
DB_PATH = os.environ.get("SLIMM_DB_PATH", "reminders.db")
DUE_CHECK_SECONDS = 5
USER_AGENT = "slimm-bot-reminders/1.0"

TRIGGER_IN = re.compile(r"^!remind\s+me\s+in\s+(\S+)\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_AT = re.compile(r"^!remind\s+me\s+at\s+(\d{1,2}:\d{2})\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_LIST = re.compile(r"^!reminders\s*$", re.IGNORECASE)
TRIGGER_CANCEL = re.compile(r"^!reminders\s+cancel\s+(\d+)\s*$", re.IGNORECASE)
DURATION_PART = re.compile(r"(\d{1,6})([smhd])")
DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


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
        """
    )
    conn.commit()
    cursor.init_table(conn)


def add_reminder(conn, reminder_id, channel_id, user_id, request_message_id, due_at, text):
    conn.execute(
        "INSERT INTO reminders "
        "(id, channel_id, user_id, request_message_id, due_at, text, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (reminder_id, channel_id, user_id, request_message_id, due_at, text, int(time.time())),
    )
    conn.commit()


def due_reminders(conn, now):
    return conn.execute(
        "SELECT id, channel_id, request_message_id, text FROM reminders "
        "WHERE sent = 0 AND cancelled = 0 AND due_at <= ?",
        (now,),
    ).fetchall()


def mark_sent(conn, reminder_id):
    conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
    conn.commit()


def pending_for_user(conn, channel_id, user_id):
    return conn.execute(
        "SELECT id, due_at, text FROM reminders "
        "WHERE channel_id = ? AND user_id = ? AND sent = 0 AND cancelled = 0 "
        "ORDER BY due_at ASC",
        (channel_id, user_id),
    ).fetchall()


def cancel_nth(conn, channel_id, user_id, n):
    """1-based, ordered the same way `!reminders` lists them."""
    rows = pending_for_user(conn, channel_id, user_id)
    if n < 1 or n > len(rows):
        return False
    conn.execute("UPDATE reminders SET cancelled = 1 WHERE id = ?", (rows[n - 1][0],))
    conn.commit()
    return True


def parse_duration(spec):
    """`2h`, `90s`, `1h30m` -> seconds, or None if `spec` is not one."""
    parts = DURATION_PART.findall(spec)
    if not parts or "".join(f"{n}{u}" for n, u in parts) != spec:
        return None
    return sum(int(n) * DURATION_SECONDS[u] for n, u in parts)


def parse_clock_time(spec, now):
    """`HH:MM` -> the next UTC epoch second that clock time falls on."""
    hour, minute = (int(part) for part in spec.split(":"))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    today = time.gmtime(now)
    candidate = time.mktime(
        (today.tm_year, today.tm_mon, today.tm_mday, hour, minute, 0, 0, 0, 0)
    ) - time.timezone
    if candidate <= now:
        candidate += 86400
    return int(candidate)


def format_due(due_at):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(due_at))


def handle_message(client, conn, me, channel_id, message):
    author_id = message.get("author_id")
    if author_id is None or author_id == me:
        return
    content = (message.get("content") or "").strip()
    request_message_id = message.get("id")

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
        due_at = parse_clock_time(spec, time.time())
        if due_at is None:
            client.send(channel_id, f"not a time I understand: `{spec}`", reply_to_id=request_message_id)
            return
        _create_and_ack(client, conn, channel_id, author_id, request_message_id, due_at, text)
        return

    if TRIGGER_LIST.match(content):
        _reply_with_pending(client, conn, channel_id, author_id, request_message_id)
        return

    if match := TRIGGER_CANCEL.match(content):
        _reply_with_cancel(client, conn, channel_id, author_id, request_message_id, int(match.group(1)))


def _reply_with_pending(client, conn, channel_id, author_id, request_message_id):
    """Answers `!reminders` with this member's own pending list, or says there is none."""
    rows = pending_for_user(conn, channel_id, author_id)
    if not rows:
        client.send(channel_id, "you have no pending reminders here", reply_to_id=request_message_id)
        return
    lines = [f"{i}. {format_due(due_at)} - {text}" for i, (_, due_at, text) in enumerate(rows, 1)]
    client.send(channel_id, "\n".join(lines), reply_to_id=request_message_id)


def _reply_with_cancel(client, conn, channel_id, author_id, request_message_id, n):
    """Cancels this member's nth pending reminder, and says so either way."""
    ok = cancel_nth(conn, channel_id, author_id, n)
    reply = f"cancelled reminder {n}" if ok else f"no reminder {n}"
    client.send(channel_id, reply, reply_to_id=request_message_id)


def _create_and_ack(client, conn, channel_id, author_id, request_message_id, due_at, text):
    reminder_id = str(uuid.uuid4())
    add_reminder(conn, reminder_id, channel_id, author_id, request_message_id, due_at, text)
    client.send(channel_id, f"will remind you at {format_due(due_at)}", reply_to_id=request_message_id)


async def due_checker(client, conn):
    while True:
        await asyncio.sleep(DUE_CHECK_SECONDS)
        for reminder_id, channel_id, request_message_id, text in due_reminders(conn, int(time.time())):
            client.send(channel_id, f"reminder: {text}", message_id=reminder_id, reply_to_id=request_message_id)
            mark_sent(conn, reminder_id)


def resync(client, conn):
    """Catches up every scoped channel over `/sync` before the socket opens,
    so a `!remind` sent while the previous session was offline is not lost."""
    scopes = [{"channel_id": c, "after_seq": cursor.get(conn, c)} for c in CHANNELS]
    me = client.me()["id"]
    for scope in cursor.sync(client, scopes):
        channel_id = scope["channel_id"]
        for message in scope["messages"]:
            handle_message(client, conn, me, channel_id, message)
        if scope["messages"]:
            cursor.set(conn, channel_id, scope["messages"][-1]["seq"])
        elif scope["reset"]:
            cursor.bootstrap(client, conn, channel_id)


async def attempt(client, conn, reset_delay):
    me = client.me()["id"]
    print(f"connected as {me}", flush=True)

    for channel_id in CHANNELS:
        cursor.bootstrap(client, conn, channel_id)
    resync(client, conn)

    async with await Connection.open(client) as socket:
        print("listening", flush=True)
        reset_delay()

        due_task = asyncio.create_task(due_checker(client, conn))
        try:
            async for frame in socket.frames():
                # Ignore a frame type we do not know; see bot-ping's docstring.
                if frame.get("type") != "message.created":
                    continue
                channel_id = frame.get("channel_id")
                if channel_id not in CHANNELS:
                    continue
                message = frame.get("message") or {}
                handle_message(client, conn, me, channel_id, message)
                seq = message.get("seq")
                if seq is not None:
                    cursor.set(conn, channel_id, seq)
        finally:
            due_task.cancel()


async def main():
    if not BASE or not TOKEN or not CHANNELS:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN and SLIMM_CHANNELS", file=sys.stderr)
        return 2

    client = Client(BASE, TOKEN, USER_AGENT)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    return await run_forever(lambda reset_delay: attempt(client, conn, reset_delay))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
