#!/usr/bin/env python3
"""A slim-m bot that sets reminders: `!remind me in 2h <text>`, `!remind me
at 15:30 <text>`, and `!reminders` to list or cancel your own.

Run it with a bot token from Space settings -> Bots, and the ids of the
channels it should watch:

    pip install websockets
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_CHANNELS=<channel-uuid>,<channel-uuid> python3 bot.py

`examples/bot-ping/` proves a bot can connect and answer, and says so in its
own docstring while listing three corners it deliberately cuts: no cursor, a
flat reconnect delay, and it answers everywhere it can see. This bot turns
all three:

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
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import websockets

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNELS = {c for c in os.environ.get("SLIMM_CHANNELS", "").split(",") if c}
DB_PATH = os.environ.get("SLIMM_DB_PATH", "reminders.db")
PROTOCOL = 1
DUE_CHECK_SECONDS = 5
MAX_BACKOFF_SECONDS = 60
# urllib's default UA is blocked by a CDN before it ever reaches slim-m.
USER_AGENT = "slimm-bot-reminders/1.0"

TRIGGER_IN = re.compile(r"^!remind\s+me\s+in\s+(\S+)\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_AT = re.compile(r"^!remind\s+me\s+at\s+(\d{1,2}:\d{2})\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_LIST = re.compile(r"^!reminders\s*$", re.IGNORECASE)
TRIGGER_CANCEL = re.compile(r"^!reminders\s+cancel\s+(\d+)\s*$", re.IGNORECASE)
DURATION_PART = re.compile(r"(\d{1,6})([smhd])")
DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def call(method, path, body=None):
    """One authenticated REST call, returning parsed JSON or None for 204."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("authorization", f"Bearer {TOKEN}")
    request.add_header("user-agent", USER_AGENT)
    if data is not None:
        request.add_header("content-type", "application/json")
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def send(channel_id, content, message_id=None, reply_to_id=None):
    """Post a message, idempotent on `message_id` so a retry after an
    uncertain send cannot double-post."""
    body = {"id": message_id or str(uuid.uuid4()), "content": content}
    if reply_to_id:
        body["reply_to_id"] = reply_to_id
    return call("POST", f"/channels/{channel_id}/messages", body)


def socket_url(base):
    """The WebSocket URL for `base`; see bot-ping's identical helper for why
    plaintext ws is refused off loopback."""
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == "https":
        return urllib.parse.urlunsplit(("wss", parts.netloc, "/ws", "", ""))
    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"):
        print("warning: plaintext ws, loopback only", file=sys.stderr)
        return urllib.parse.urlunsplit(("ws", parts.netloc, "/ws", "", ""))
    raise RuntimeError(
        f"refusing to send a bot token over {parts.scheme or 'no'} scheme to "
        f"{parts.hostname or base}; use https"
    )


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
        CREATE TABLE IF NOT EXISTS cursors (
            channel_id TEXT PRIMARY KEY,
            after_seq INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def get_cursor(conn, channel_id):
    row = conn.execute(
        "SELECT after_seq FROM cursors WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    return row[0] if row else None


def set_cursor(conn, channel_id, seq):
    conn.execute(
        "INSERT INTO cursors (channel_id, after_seq) VALUES (?, ?) "
        "ON CONFLICT(channel_id) DO UPDATE SET after_seq = excluded.after_seq "
        "WHERE excluded.after_seq > after_seq",
        (channel_id, seq),
    )
    conn.commit()


def bootstrap_cursor(conn, channel_id):
    """A channel this bot has never watched starts at its current head, not
    at the beginning of history - see the module docstring's cursor note."""
    if get_cursor(conn, channel_id) is not None:
        return
    latest = call("GET", f"/channels/{channel_id}/messages?limit=1")
    seq = latest[0]["seq"] if latest else 0
    set_cursor(conn, channel_id, seq)


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


def handle_message(conn, me, channel_id, message):
    author_id = message.get("author_id")
    if author_id is None or author_id == me:
        return
    content = (message.get("content") or "").strip()
    request_message_id = message.get("id")

    if match := TRIGGER_IN.match(content):
        spec, text = match.groups()
        seconds = parse_duration(spec)
        if seconds is None or seconds <= 0:
            send(channel_id, f"not a duration I understand: `{spec}`", reply_to_id=request_message_id)
            return
        due_at = int(time.time()) + seconds
        _create_and_ack(conn, channel_id, author_id, request_message_id, due_at, text)
        return

    if match := TRIGGER_AT.match(content):
        spec, text = match.groups()
        due_at = parse_clock_time(spec, time.time())
        if due_at is None:
            send(channel_id, f"not a time I understand: `{spec}`", reply_to_id=request_message_id)
            return
        _create_and_ack(conn, channel_id, author_id, request_message_id, due_at, text)
        return

    if TRIGGER_LIST.match(content):
        _reply_with_pending(conn, channel_id, author_id, request_message_id)
        return

    if match := TRIGGER_CANCEL.match(content):
        _reply_with_cancel(conn, channel_id, author_id, request_message_id, int(match.group(1)))


def _reply_with_pending(conn, channel_id, author_id, request_message_id):
    """Answers `!reminders` with this member's own pending list, or says there is none."""
    rows = pending_for_user(conn, channel_id, author_id)
    if not rows:
        send(channel_id, "you have no pending reminders here", reply_to_id=request_message_id)
        return
    lines = [f"{i}. {format_due(due_at)} - {text}" for i, (_, due_at, text) in enumerate(rows, 1)]
    send(channel_id, "\n".join(lines), reply_to_id=request_message_id)


def _reply_with_cancel(conn, channel_id, author_id, request_message_id, n):
    """Cancels this member's nth pending reminder, and says so either way."""
    ok = cancel_nth(conn, channel_id, author_id, n)
    reply = f"cancelled reminder {n}" if ok else f"no reminder {n}"
    send(channel_id, reply, reply_to_id=request_message_id)


def _create_and_ack(conn, channel_id, author_id, request_message_id, due_at, text):
    reminder_id = str(uuid.uuid4())
    add_reminder(conn, reminder_id, channel_id, author_id, request_message_id, due_at, text)
    send(channel_id, f"will remind you at {format_due(due_at)}", reply_to_id=request_message_id)


async def due_checker(conn):
    while True:
        await asyncio.sleep(DUE_CHECK_SECONDS)
        for reminder_id, channel_id, request_message_id, text in due_reminders(conn, int(time.time())):
            send(channel_id, f"reminder: {text}", message_id=reminder_id, reply_to_id=request_message_id)
            mark_sent(conn, reminder_id)


def resync(conn):
    """Catches up every scoped channel over `/sync` before the socket opens,
    so a `!remind` sent while the previous session was offline is not lost."""
    scopes = [{"channel_id": c, "after_seq": get_cursor(conn, c)} for c in CHANNELS]
    response = call("POST", "/sync", {"scopes": scopes})
    me = call("GET", "/me")["id"]
    for scope in response["scopes"]:
        channel_id = scope["channel_id"]
        for message in scope["messages"]:
            handle_message(conn, me, channel_id, message)
        if scope["messages"]:
            set_cursor(conn, channel_id, scope["messages"][-1]["seq"])
        elif scope["reset"]:
            bootstrap_cursor(conn, channel_id)


async def listen(conn, on_connected):
    me = call("GET", "/me")["id"]
    print(f"connected as {me}", flush=True)

    for channel_id in CHANNELS:
        bootstrap_cursor(conn, channel_id)
    resync(conn)

    ticket = call("POST", "/auth/ws-ticket")["ticket"]
    ws_url = socket_url(BASE)

    async with websockets.connect(ws_url, user_agent_header=USER_AGENT) as socket:
        await socket.send(json.dumps({"type": "hello", "ticket": ticket, "protocol": PROTOCOL}))
        hello = json.loads(await socket.recv())
        if hello.get("type") != "hello":
            raise RuntimeError(f"expected a hello back, got {hello}")
        print("listening", flush=True)
        on_connected()

        due_task = asyncio.create_task(due_checker(conn))
        try:
            async for raw in socket:
                frame = json.loads(raw)
                # Ignore a frame type we do not know; see bot-ping's docstring.
                if frame.get("type") != "message.created":
                    continue
                channel_id = frame.get("channel_id")
                if channel_id not in CHANNELS:
                    continue
                message = frame.get("message") or {}
                handle_message(conn, me, channel_id, message)
                seq = message.get("seq")
                if seq is not None:
                    set_cursor(conn, channel_id, seq)
        finally:
            due_task.cancel()


async def main():
    if not BASE or not TOKEN or not CHANNELS:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN and SLIMM_CHANNELS", file=sys.stderr)
        return 2

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    delay = 1

    def reset_delay():
        nonlocal delay
        delay = 1

    while True:
        try:
            await listen(conn, reset_delay)
        except urllib.error.HTTPError as err:
            # 401 means the token was revoked; there is nothing to retry.
            if err.code == 401:
                print("token rejected - revoked?", file=sys.stderr)
                return 1
            print(f"http {err.code}, retrying in {delay}s", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying in {delay}s", file=sys.stderr)
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_BACKOFF_SECONDS)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
