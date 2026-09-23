#!/usr/bin/env python3
"""A slim-m bot that keeps a todo board on a channel's Voice Canvas:
`!board add <text>` places a sticky note, `!board done <n>` removes it,
`!board move <n> <slot>` repositions it, and `!board` lists what is up.

Run it with a bot token from Space settings -> Bots, holding `SEND_MESSAGES`,
`VIEW_CHANNEL` and `USE_CANVAS` on the one channel it watches:

    pip install -r requirements.txt
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_CHANNEL=<channel-uuid> python3 bot.py

`examples/bot-reminders/` and `examples/bot-roles/` both only ever call
`POST /channels/{id}/messages`. This bot exercises the other write surface a
bot's default grant already reaches: `POST .../canvas/objects` (place),
`POST .../canvas/ops` (move, remove) and `GET .../canvas/objects` (the
viewport read), plus the three canvas events on the wire
(`canvas.object.placed`, `canvas.objects.removed`, `canvas.cleared`).

## The board is a fixed strip of the shared, near-infinite canvas

Every note this bot places lands inside one small rectangle at a fixed
origin, one `MAX_SLOTS`-tall column. That is not a platform limit, it is
this bot's own choice, and it is what makes reconciliation cheap: on every
(re)connect it re-reads that one bounded rectangle with a single viewport
query and treats it as ground truth, rather than tracking a `seq` cursor
into the canvas op stream the way `bot-reminders` tracks one into a
channel's messages. `!board move` only ever retargets a note to another slot
in that same column for exactly this reason - a note moved to an arbitrary
world coordinate could drift outside the rectangle this bot re-reads, and
this bot would then read its own disappearance from the query as a removal.

## A note's text is create-only

There is no edit route for a canvas object of any kind - a note is written
once at `POST .../canvas/objects` and never again; the wire event
[`CanvasObjectDto`] confirms it carries no updated-content event either.
`!board move` works around this only because a *position* can change
without a new object (`POST .../canvas/ops` `kind: "move"`), authorized on
the same `USE_CANVAS` bit as the note's own author needs no `MANAGE_CANVAS`
for. Changing a note's *text* has no such path: it costs a real remove and
replace, and this bot does not offer that as a single command, because
`!board done <n>` followed by `!board add <text>` already is that, in the
open rather than hidden behind an "edit" that quietly changes the note's id.

## Reconciling with what actually happened while disconnected

A `canvas.objects.removed` frame carries the ids removed; a `canvas.cleared`
frame carries none at all, only a `before_seq` - deliberately, since a clear
can cover a channel's whole live ceiling and the hub's broadcast ring is
sized against a bounded frame, not against how much of a canvas one clear
can wipe. So the only way this bot can tell whether *its own* notes were
part of a clear it was offline for, or missed live, is to keep each note's
own `seq` from the moment it was placed and compare that against
`before_seq` on reconcile - which is exactly what `reconcile()` below does;
there is nothing on the wire that would tell it more directly.

What this deliberately does not do:

- **A note placed by a human landing in the board's rectangle.** This bot
  reconciles only objects whose `author_id` is its own id; anything else in
  that rectangle is left completely alone, including on `!board add`'s slot
  search, which can then place a note overlapping one it does not own.
- **Restoring an undone remove or clear.** `canvas.objects.restored` is not
  handled; a moderator's undo brings a note back on everyone's canvas but
  this bot's own board state stays as though it were still gone. A fork
  wanting this can treat that frame the same way `canvas.objects.removed`
  is treated below, in reverse.
- **A board bigger than `MAX_SLOTS` items**, or more than one board per
  channel. `!board add` refuses once every slot is taken rather than
  growing the column, since an unbounded column is an unbounded viewport
  query too.
- **Everything `bot-reminders` already covers and this bot does not repeat
  differently**: exponential backoff, `SLIMM_CHANNEL` scoping, and a
  terminal 401. See its own docstring.
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
CHANNEL = os.environ.get("SLIMM_CHANNEL", "")
DB_PATH = os.environ.get("SLIMM_DB_PATH", "board.db")
PROTOCOL = 1
MAX_BACKOFF_SECONDS = 60
# urllib's default UA is blocked by a CDN before it ever reaches slim-m.
USER_AGENT = "slimm-bot-canvas-board/1.0"

# A note's box is 220x140, the app's own quick-placement default, so a note this bot places renders identically to a hand-drawn one.
BOARD_X = 0.0
BOARD_Y = 0.0
NOTE_W = 220.0
NOTE_H = 140.0
GAP = 20.0
MAX_SLOTS = 20

TRIGGER_ADD = re.compile(r"^!board\s+add\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_DONE = re.compile(r"^!board\s+done\s+(\d+)\s*$", re.IGNORECASE)
TRIGGER_MOVE = re.compile(r"^!board\s+move\s+(\d+)\s+(\d+)\s*$", re.IGNORECASE)
TRIGGER_LIST = re.compile(r"^!board(?:\s+list)?\s*$", re.IGNORECASE)


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
    """Post a message, idempotent on `message_id`."""
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
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            slot INTEGER NOT NULL,
            text TEXT NOT NULL,
            seq INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS cursor (
            channel_id TEXT PRIMARY KEY,
            after_seq INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def get_cursor(conn):
    row = conn.execute(
        "SELECT after_seq FROM cursor WHERE channel_id = ?", (CHANNEL,)
    ).fetchone()
    return row[0] if row else None


def set_cursor(conn, seq):
    conn.execute(
        "INSERT INTO cursor (channel_id, after_seq) VALUES (?, ?) "
        "ON CONFLICT(channel_id) DO UPDATE SET after_seq = excluded.after_seq "
        "WHERE excluded.after_seq > after_seq",
        (CHANNEL, seq),
    )
    conn.commit()


def bootstrap_cursor(conn):
    """A first run starts at the channel's current head; see bot-reminders'
    identical reasoning for why replaying all of history would be wrong."""
    if get_cursor(conn) is not None:
        return
    latest = call("GET", f"/channels/{CHANNEL}/messages?limit=1")
    set_cursor(conn, latest[0]["seq"] if latest else 0)


def slot_y(slot):
    return BOARD_Y + slot * (NOTE_H + GAP)


def active_items(conn):
    return conn.execute(
        "SELECT id, slot, text, seq FROM items WHERE active = 1 ORDER BY slot"
    ).fetchall()


def active_by_slot(conn, slot):
    return conn.execute(
        "SELECT id, text, seq FROM items WHERE active = 1 AND slot = ?", (slot,)
    ).fetchone()


def free_slot(conn):
    taken = {row[0] for row in conn.execute("SELECT slot FROM items WHERE active = 1")}
    for slot in range(MAX_SLOTS):
        if slot not in taken:
            return slot
    return None


def reconcile(conn):
    """Re-reads the board's rectangle and makes it ground truth: a note this
    bot no longer sees there - removed live, removed while disconnected, or
    swept up in a clear this bot's own `seq` bookkeeping below did not catch -
    is marked inactive, freeing its slot."""
    me = call("GET", "/me")["id"]
    params = urllib.parse.urlencode(
        {
            "min_x": BOARD_X - 1,
            "min_y": BOARD_Y - 1,
            "max_x": BOARD_X + NOTE_W + 1,
            "max_y": slot_y(MAX_SLOTS) + 1,
            "limit": MAX_SLOTS + 5,
        }
    )
    viewport = call("GET", f"/channels/{CHANNEL}/canvas/objects?{params}")
    seen_ids = set()
    for obj in viewport["objects"]:
        if obj["kind"] != "note" or obj.get("author_id") != me:
            continue
        slot = round((obj["y"] - BOARD_Y) / (NOTE_H + GAP))
        text = obj["props"].get("text", "")
        seen_ids.add(obj["id"])
        conn.execute(
            "INSERT INTO items (id, slot, text, seq, active) VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(id) DO UPDATE SET slot = excluded.slot, seq = excluded.seq, active = 1",
            (obj["id"], slot, text, obj["seq"]),
        )
    stale = [row[0] for row in active_items(conn) if row[0] not in seen_ids]
    for item_id in stale:
        conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()
    return me


def add_item(conn, channel_id, request_message_id, text):
    slot = free_slot(conn)
    if slot is None:
        send(channel_id, f"board is full ({MAX_SLOTS} items)", reply_to_id=request_message_id)
        return
    item_id = str(uuid.uuid4())
    placed = call(
        "POST",
        f"/channels/{CHANNEL}/canvas/objects",
        {
            "id": item_id,
            "kind": "note",
            "x": BOARD_X,
            "y": slot_y(slot),
            "w": NOTE_W,
            "h": NOTE_H,
            "props": {"text": text},
        },
    )
    conn.execute(
        "INSERT INTO items (id, slot, text, seq, active) VALUES (?, ?, ?, ?, 1)",
        (item_id, slot, text, placed["seq"]),
    )
    conn.commit()
    send(channel_id, f"added as #{slot + 1}: {text}", reply_to_id=request_message_id)


def done_item(conn, channel_id, request_message_id, n):
    row = active_by_slot(conn, n - 1)
    if row is None:
        send(channel_id, f"no item #{n}", reply_to_id=request_message_id)
        return
    item_id, text, _ = row
    call(
        "POST",
        f"/channels/{CHANNEL}/canvas/ops",
        {"id": str(uuid.uuid4()), "kind": "remove", "object_ids": [item_id]},
    )
    conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()
    send(channel_id, f"done: {text}", reply_to_id=request_message_id)


def move_item(conn, channel_id, request_message_id, n, to):
    if not (1 <= to <= MAX_SLOTS):
        send(channel_id, f"slot must be 1-{MAX_SLOTS}", reply_to_id=request_message_id)
        return
    row = active_by_slot(conn, n - 1)
    if row is None:
        send(channel_id, f"no item #{n}", reply_to_id=request_message_id)
        return
    if active_by_slot(conn, to - 1) is not None:
        send(channel_id, f"#{to} is already taken", reply_to_id=request_message_id)
        return
    item_id, text, _ = row
    call(
        "POST",
        f"/channels/{CHANNEL}/canvas/ops",
        {
            "id": str(uuid.uuid4()),
            "kind": "move",
            "object_id": item_id,
            "x": BOARD_X,
            "y": slot_y(to - 1),
            "w": NOTE_W,
            "h": NOTE_H,
        },
    )
    conn.execute("UPDATE items SET slot = ? WHERE id = ?", (to - 1, item_id))
    conn.commit()
    send(channel_id, f"moved #{n} to #{to}: {text}", reply_to_id=request_message_id)


def list_items(conn, channel_id, request_message_id):
    rows = active_items(conn)
    if not rows:
        send(channel_id, "the board is empty", reply_to_id=request_message_id)
        return
    lines = [f"#{slot + 1}: {text}" for _, slot, text, _ in rows]
    send(channel_id, "\n".join(lines), reply_to_id=request_message_id)


def handle_message(conn, me, message):
    author_id = message.get("author_id")
    if author_id is None or author_id == me:
        return
    content = (message.get("content") or "").strip()
    channel_id = message.get("channel_id") or CHANNEL
    request_message_id = message.get("id")

    if match := TRIGGER_ADD.match(content):
        add_item(conn, channel_id, request_message_id, match.group(1))
    elif match := TRIGGER_DONE.match(content):
        done_item(conn, channel_id, request_message_id, int(match.group(1)))
    elif match := TRIGGER_MOVE.match(content):
        move_item(conn, channel_id, request_message_id, int(match.group(1)), int(match.group(2)))
    elif TRIGGER_LIST.match(content):
        list_items(conn, channel_id, request_message_id)


def handle_canvas_removed(conn, before_seq, object_ids):
    ids = set(object_ids)
    for item_id, _, _, seq in active_items(conn):
        if item_id in ids or (before_seq is not None and seq <= before_seq):
            conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()


def resync(conn, me):
    """Catches up messages sent to this channel while disconnected, the same
    shape `bot-reminders` uses for the same reason."""
    response = call(
        "POST", "/sync", {"scopes": [{"channel_id": CHANNEL, "after_seq": get_cursor(conn)}]}
    )
    scope = response["scopes"][0]
    for message in scope["messages"]:
        handle_message(conn, me, message)
    if scope["messages"]:
        set_cursor(conn, scope["messages"][-1]["seq"])
    elif scope["reset"]:
        bootstrap_cursor(conn)


async def listen(conn, on_connected):
    bootstrap_cursor(conn)
    me = reconcile(conn)
    resync(conn, me)
    print(f"connected as {me}", flush=True)

    ticket = call("POST", "/auth/ws-ticket")["ticket"]
    async with websockets.connect(socket_url(BASE), user_agent_header=USER_AGENT) as socket:
        await socket.send(json.dumps({"type": "hello", "ticket": ticket, "protocol": PROTOCOL}))
        hello = json.loads(await socket.recv())
        if hello.get("type") != "hello":
            raise RuntimeError(f"expected a hello back, got {hello}")
        print("listening", flush=True)
        on_connected()

        async for raw in socket:
            frame = json.loads(raw)
            frame_type = frame.get("type")
            # Ignore a frame type we do not know; see bot-ping's docstring.
            if frame_type == "message.created" and frame.get("channel_id") == CHANNEL:
                message = frame.get("message") or {}
                handle_message(conn, me, message)
                if message.get("seq") is not None:
                    set_cursor(conn, message["seq"])
            elif frame_type == "canvas.objects.removed" and frame.get("channel_id") == CHANNEL:
                handle_canvas_removed(conn, None, frame.get("object_ids") or [])
            elif frame_type == "canvas.cleared" and frame.get("channel_id") == CHANNEL:
                handle_canvas_removed(conn, frame.get("before_seq"), [])


async def main():
    if not BASE or not TOKEN or not CHANNEL:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN and SLIMM_CHANNEL", file=sys.stderr)
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
