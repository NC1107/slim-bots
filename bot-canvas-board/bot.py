#!/usr/bin/env python3
"""A slim-m bot that keeps a todo board on a channel's Voice Canvas:
`!board add <text>` places a sticky note, `!board done <n>` removes it,
`!board move <n> <slot>` repositions it, `!board clear yes` wipes the whole
board, and `!board` lists what is up, crediting whoever added each note.

Run it with a bot token from Space settings -> Bots, holding `SEND_MESSAGES`,
`VIEW_CHANNEL` and `USE_CANVAS` on the one channel it watches:

    pip install -r requirements.txt
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_CHANNEL=<channel-uuid> python3 bot.py

`bot-reminders/` and `bot-roles/` both only ever call
`POST /channels/{id}/messages`. This bot exercises the other write surface a
bot's default grant already reaches: `POST .../canvas/objects` (place),
`POST .../canvas/ops` (move, remove) and `GET .../canvas/objects` (the
viewport read), plus the three canvas events on the wire
(`canvas.object.placed`, `canvas.objects.removed`, `canvas.cleared`).

Auth, the REST call, the websocket handshake, the reconnect loop with
backoff, and the `seq`-cursor sqlite table for `SLIMM_CHANNEL`'s message
traffic all come from the `slimbots` package (`../slimbots/`) - the same
plumbing `bot-reminders` and `bot-roles` use. The canvas-specific
reconciliation below has no library counterpart: it is this bot's own
business logic, not shared plumbing.

## The board is a fixed strip of the shared, near-infinite canvas

Every note this bot places lands inside one small rectangle at a fixed
origin, one `MAX_SLOTS`-tall column. That is not a platform limit, it is
this bot's own choice, and it is what makes reconciliation cheap: on every
(re)connect it re-reads that one bounded rectangle with a single viewport
query and treats it as ground truth, rather than tracking a `seq` cursor
into the canvas op stream the way this bot tracks one into a channel's
messages via `slimbots.cursor`. `!board move` only ever retargets a note to
another slot in that same column for exactly this reason - a note moved to
an arbitrary world coordinate could drift outside the rectangle this bot
re-reads, and this bot would then read its own disappearance from the query
as a removal.

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

## Safeguards added without losing any of the above

- **A length bound on note text.** `MAX_TEXT_LENGTH` refuses text past what a
  fixed 220x140 box could ever show usefully, before it ever reaches the
  canvas API - not a platform limit, just this bot declining to place
  something it already knows will overflow its own box.
- **A confirmation on `!board clear`.** Bare `!board clear` explains what it
  would do and how many items it would remove, and does nothing; only
  `!board clear yes` actually removes them. One command wiping every item on
  a shared board deserved more friction than every other command here.
- **A usage hint instead of silence.** Anything starting with `!board` that
  does not match a known command - `!board add` with no text, a typo, a bare
  `!board help` - gets `HELP_TEXT` back rather than nothing at all.
- **It never answers another bot.** `slimbots.AuthorFilter` skips every
  automated author, not just itself, so several bots sharing a channel
  cannot loop through this one's replies.
"""

import asyncio
import os
import re
import sqlite3
import sys
import urllib.parse
import uuid

from slimbots import AuthorFilter, Client, Connection, cursor, run_forever

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLIMM_CHANNEL", "")
DB_PATH = os.environ.get("SLIMM_DB_PATH", "board.db")
USER_AGENT = "slimm-bot-canvas-board/1.0"

# A note's box is 220x140, the app's own quick-placement default, so a note this bot places renders identically to a hand-drawn one.
BOARD_X = 0.0
BOARD_Y = 0.0
NOTE_W = 220.0
NOTE_H = 140.0
GAP = 20.0
MAX_SLOTS = 20
# A note is a fixed 220x140 box; text past this is going to overflow it regardless of what the canvas itself allows.
MAX_TEXT_LENGTH = 240

TRIGGER_ADD = re.compile(r"^!board\s+add\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_DONE = re.compile(r"^!board\s+done\s+(\d+)\s*$", re.IGNORECASE)
TRIGGER_MOVE = re.compile(r"^!board\s+move\s+(\d+)\s+(\d+)\s*$", re.IGNORECASE)
TRIGGER_CLEAR = re.compile(r"^!board\s+clear(?:\s+(yes))?\s*$", re.IGNORECASE)
TRIGGER_HELP = re.compile(r"^!board\s+help\s*$", re.IGNORECASE)
TRIGGER_LIST = re.compile(r"^!board(?:\s+list)?\s*$", re.IGNORECASE)
TRIGGER_ANY = re.compile(r"^!board\b", re.IGNORECASE)

HELP_TEXT = (
    "commands: `!board` to list, `!board add <text>`, `!board done <n>`, "
    "`!board move <n> <slot>`, `!board clear yes` (removes everything - needs the "
    "confirmation word)."
)

# user_id -> display_name, resolved once per author and reused; a later rename keeps the old name.
_names = {}


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            slot INTEGER NOT NULL,
            text TEXT NOT NULL,
            seq INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            added_by TEXT
        );
        """
    )
    conn.commit()
    cursor.init_table(conn)


def name_of(client, author_id):
    """A display name for crediting who added an item, resolved once and
    reused. `None` if the lookup fails - a name is a nice-to-have on a note,
    never worth blocking the note over."""
    if author_id not in _names:
        try:
            _names[author_id] = client.call("GET", f"/users/{author_id}")["display_name"]
        except Exception:
            return None
    return _names[author_id]


def slot_y(slot):
    return BOARD_Y + slot * (NOTE_H + GAP)


def active_items(conn):
    return conn.execute(
        "SELECT id, slot, text, seq, added_by FROM items WHERE active = 1 ORDER BY slot"
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


def reconcile(client, conn):
    """Re-reads the board's rectangle and makes it ground truth: a note this
    bot no longer sees there - removed live, removed while disconnected, or
    swept up in a clear this bot's own `seq` bookkeeping below did not catch -
    is marked inactive, freeing its slot."""
    me = client.me()["id"]
    params = urllib.parse.urlencode(
        {
            "min_x": BOARD_X - 1,
            "min_y": BOARD_Y - 1,
            "max_x": BOARD_X + NOTE_W + 1,
            "max_y": slot_y(MAX_SLOTS) + 1,
            "limit": MAX_SLOTS + 5,
        }
    )
    viewport = client.call("GET", f"/channels/{CHANNEL}/canvas/objects?{params}")
    seen_ids = set()
    for obj in viewport["objects"]:
        if obj["kind"] != "note" or obj.get("author_id") != me:
            continue
        slot = round((obj["y"] - BOARD_Y) / (NOTE_H + GAP))
        text = obj["props"].get("text", "")
        seen_ids.add(obj["id"])
        conn.execute(
            "INSERT INTO items (id, slot, text, seq, active, added_by) VALUES (?, ?, ?, ?, 1, NULL) "
            "ON CONFLICT(id) DO UPDATE SET slot = excluded.slot, seq = excluded.seq, active = 1",
            (obj["id"], slot, text, obj["seq"]),
        )
    stale = [row[0] for row in active_items(conn) if row[0] not in seen_ids]
    for item_id in stale:
        conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()
    return me


def add_item(client, conn, channel_id, request_message_id, author_id, text):
    if len(text) > MAX_TEXT_LENGTH:
        client.send(
            channel_id,
            f"that's {len(text)} characters, {MAX_TEXT_LENGTH} max - a note is a fixed-size box.",
            reply_to_id=request_message_id,
        )
        return
    slot = free_slot(conn)
    if slot is None:
        client.send(channel_id, f"board is full ({MAX_SLOTS} items)", reply_to_id=request_message_id)
        return
    item_id = str(uuid.uuid4())
    placed = client.call(
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
        "INSERT INTO items (id, slot, text, seq, active, added_by) VALUES (?, ?, ?, ?, 1, ?)",
        (item_id, slot, text, placed["seq"], author_id),
    )
    conn.commit()
    client.send(channel_id, f"added as #{slot + 1}: {text}", reply_to_id=request_message_id)


def done_item(client, conn, channel_id, request_message_id, n):
    row = active_by_slot(conn, n - 1)
    if row is None:
        client.send(channel_id, f"no item #{n}", reply_to_id=request_message_id)
        return
    item_id, text, _ = row
    client.call(
        "POST",
        f"/channels/{CHANNEL}/canvas/ops",
        {"id": str(uuid.uuid4()), "kind": "remove", "object_ids": [item_id]},
    )
    conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()
    client.send(channel_id, f"done: {text}", reply_to_id=request_message_id)


def clear_board(client, conn, channel_id, request_message_id, confirmed):
    rows = active_items(conn)
    if not rows:
        client.send(channel_id, "the board is already empty", reply_to_id=request_message_id)
        return
    if not confirmed:
        client.send(
            channel_id,
            f"this removes all {len(rows)} item(s) on the board - resend as `!board clear yes` to confirm.",
            reply_to_id=request_message_id,
        )
        return
    item_ids = [row[0] for row in rows]
    client.call(
        "POST",
        f"/channels/{CHANNEL}/canvas/ops",
        {"id": str(uuid.uuid4()), "kind": "remove", "object_ids": item_ids},
    )
    conn.executemany("UPDATE items SET active = 0 WHERE id = ?", [(item_id,) for item_id in item_ids])
    conn.commit()
    client.send(channel_id, f"cleared {len(item_ids)} item(s).", reply_to_id=request_message_id)


def move_item(client, conn, channel_id, request_message_id, n, to):
    if not (1 <= to <= MAX_SLOTS):
        client.send(channel_id, f"slot must be 1-{MAX_SLOTS}", reply_to_id=request_message_id)
        return
    row = active_by_slot(conn, n - 1)
    if row is None:
        client.send(channel_id, f"no item #{n}", reply_to_id=request_message_id)
        return
    if active_by_slot(conn, to - 1) is not None:
        client.send(channel_id, f"#{to} is already taken", reply_to_id=request_message_id)
        return
    item_id, text, _ = row
    client.call(
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
    client.send(channel_id, f"moved #{n} to #{to}: {text}", reply_to_id=request_message_id)


def list_items(client, conn, channel_id, request_message_id):
    rows = active_items(conn)
    if not rows:
        client.send(channel_id, "the board is empty", reply_to_id=request_message_id)
        return
    lines = []
    for _, slot, text, _, added_by in rows:
        name = name_of(client, added_by) if added_by else None
        lines.append(f"#{slot + 1}: {text} (added by {name})" if name else f"#{slot + 1}: {text}")
    client.send(channel_id, "\n".join(lines), reply_to_id=request_message_id)


def handle_message(client, conn, me, authors, message):
    """The author check stops the bot answering itself; `authors.should_handle`
    stops it answering another bot in the fleet the same way bot-ping does."""
    author_id = message.get("author_id")
    if not authors.should_handle(author_id, me):
        return
    content = (message.get("content") or "").strip()
    channel_id = message.get("channel_id") or CHANNEL
    request_message_id = message.get("id")

    if match := TRIGGER_ADD.match(content):
        add_item(client, conn, channel_id, request_message_id, author_id, match.group(1))
    elif match := TRIGGER_DONE.match(content):
        done_item(client, conn, channel_id, request_message_id, int(match.group(1)))
    elif match := TRIGGER_MOVE.match(content):
        move_item(client, conn, channel_id, request_message_id, int(match.group(1)), int(match.group(2)))
    elif match := TRIGGER_CLEAR.match(content):
        clear_board(client, conn, channel_id, request_message_id, confirmed=match.group(1) is not None)
    elif TRIGGER_HELP.match(content):
        client.send(channel_id, HELP_TEXT, reply_to_id=request_message_id)
    elif TRIGGER_LIST.match(content):
        list_items(client, conn, channel_id, request_message_id)
    elif TRIGGER_ANY.match(content):
        client.send(channel_id, HELP_TEXT, reply_to_id=request_message_id)


def handle_canvas_removed(conn, before_seq, object_ids):
    ids = set(object_ids)
    for item_id, _, _, seq, _ in active_items(conn):
        if item_id in ids or (before_seq is not None and seq <= before_seq):
            conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()


def resync(client, conn, me, authors):
    """Catches up messages sent to this channel while disconnected, the same
    shape `bot-reminders` uses for the same reason."""
    scopes = cursor.sync(client, [{"channel_id": CHANNEL, "after_seq": cursor.get(conn, CHANNEL)}])
    scope = scopes[0]
    for message in scope["messages"]:
        handle_message(client, conn, me, authors, message)
    if scope["messages"]:
        cursor.set(conn, CHANNEL, scope["messages"][-1]["seq"])
    elif scope["reset"]:
        cursor.bootstrap(client, conn, CHANNEL)


async def attempt(client, conn, authors, reset_delay):
    cursor.bootstrap(client, conn, CHANNEL)
    me = reconcile(client, conn)
    resync(client, conn, me, authors)
    print(f"connected as {me}", flush=True)

    async with await Connection.open(client) as socket:
        print("listening", flush=True)
        reset_delay()

        async for frame in socket.frames():
            frame_type = frame.get("type")
            # Ignore a frame type we do not know; see bot-ping's docstring.
            if frame_type == "message.created" and frame.get("channel_id") == CHANNEL:
                message = frame.get("message") or {}
                handle_message(client, conn, me, authors, message)
                if message.get("seq") is not None:
                    cursor.set(conn, CHANNEL, message["seq"])
            elif frame_type == "canvas.objects.removed" and frame.get("channel_id") == CHANNEL:
                handle_canvas_removed(conn, None, frame.get("object_ids") or [])
            elif frame_type == "canvas.cleared" and frame.get("channel_id") == CHANNEL:
                handle_canvas_removed(conn, frame.get("before_seq"), [])


async def main():
    if not BASE or not TOKEN or not CHANNEL:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN and SLIMM_CHANNEL", file=sys.stderr)
        return 2

    client = Client(BASE, TOKEN, USER_AGENT)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    authors = AuthorFilter(client)

    return await run_forever(lambda reset_delay: attempt(client, conn, authors, reset_delay))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
