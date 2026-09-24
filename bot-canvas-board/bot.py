#!/usr/bin/env python3
"""bot-canvas-board: a todo board on a channel's Voice Canvas - `!board add/done/move/clear/list`; see README.md."""

from slimbots import ApiError, Bot, Canvas
from slimbots.migrations import ensure_columns

# A note's box is 220x140, the app's own quick-placement default, so a note this bot places renders identically to a hand-drawn one.
BOARD_X = 0.0
BOARD_Y = 0.0
NOTE_W = 220.0
NOTE_H = 140.0
GAP = 20.0
MAX_SLOTS = 20
# A note is a fixed 220x140 box; text past this overflows it regardless of what the canvas itself allows.
MAX_TEXT_LENGTH = 240

HELP_TEXT = (
    "commands: `!board` to list, `!board add <text>`, `!board done <n>`, "
    "`!board move <n> <slot>`, `!board clear` (removes everything - asks to confirm)."
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
    ensure_columns(conn, "items", {"added_by": "TEXT"})
    conn.commit()


bot = Bot(prefix="!", require_channels=True, default_data_path="board.db", store_migrate=init_db)
canvas = None  # set once bot.channel is known, in on_connect


async def name_of(author_id):
    """A display name for crediting who added an item; None if the lookup fails - never worth blocking the note."""
    if author_id not in _names:
        try:
            member = await bot.space.fetch_member(author_id)
        except ApiError:
            return None
        _names[author_id] = member.display_name
    return _names[author_id]


def slot_y(slot):
    return BOARD_Y + slot * (NOTE_H + GAP)


def active_items(conn):
    return conn.execute("SELECT id, slot, text, seq, added_by FROM items WHERE active = 1 ORDER BY slot").fetchall()


def active_by_slot(conn, slot):
    return conn.execute("SELECT id, text, seq FROM items WHERE active = 1 AND slot = ?", (slot,)).fetchone()


def free_slot(conn):
    taken = {row[0] for row in conn.execute("SELECT slot FROM items WHERE active = 1")}
    for slot in range(MAX_SLOTS):
        if slot not in taken:
            return slot
    return None


def _txn_reconcile(conn, objects, me_id):
    seen_ids = set()
    for obj in objects:
        if obj["kind"] != "note" or obj.get("author_id") != me_id:
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


async def reconcile():
    """Re-reads the board's rectangle and makes it ground truth; see README.md."""
    viewport = await canvas.viewport(min_x=BOARD_X - 1, min_y=BOARD_Y - 1, max_x=BOARD_X + NOTE_W + 1, max_y=slot_y(MAX_SLOTS) + 1, limit=MAX_SLOTS + 5)
    await bot.store.run(_txn_reconcile, viewport["objects"], bot.me_id)


def _insert_item(conn, item_id, slot, text, seq, added_by):
    conn.execute(
        "INSERT INTO items (id, slot, text, seq, active, added_by) VALUES (?, ?, ?, ?, 1, ?)",
        (item_id, slot, text, seq, added_by),
    )
    conn.commit()


async def add_item(ctx, text):
    if len(text) > MAX_TEXT_LENGTH:
        await ctx.reply(f"that's {len(text)} characters, {MAX_TEXT_LENGTH} max - a note is a fixed-size box.")
        return
    slot = await bot.store.run(free_slot)
    if slot is None:
        await ctx.reply(f"board is full ({MAX_SLOTS} items)")
        return
    placed = await canvas.place("note", x=BOARD_X, y=slot_y(slot), w=NOTE_W, h=NOTE_H, props={"text": text})
    await bot.store.run(_insert_item, placed["id"], slot, text, placed["seq"], ctx.author.id)
    await ctx.reply(f"added as #{slot + 1}: {text}")


def _deactivate_item(conn, item_id):
    conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()


async def done_item(ctx, n):
    row = await bot.store.run(active_by_slot, n - 1)
    if row is None:
        await ctx.reply(f"no item #{n}")
        return
    item_id, text, _ = row
    await canvas.remove([item_id])
    await bot.store.run(_deactivate_item, item_id)
    await ctx.reply(f"done: {text}")


def _deactivate_items(conn, item_ids):
    conn.executemany("UPDATE items SET active = 0 WHERE id = ?", [(item_id,) for item_id in item_ids])
    conn.commit()


async def clear_board(ctx):
    rows = await bot.store.run(active_items)
    if not rows:
        await ctx.reply("the board is already empty")
        return
    if not await ctx.confirm(f"This removes all {len(rows)} item(s) on the board."):
        await ctx.reply("cancelled")
        return
    item_ids = [row[0] for row in rows]
    await canvas.remove(item_ids)
    await bot.store.run(_deactivate_items, item_ids)
    await ctx.reply(f"cleared {len(item_ids)} item(s).")


def _check_move(conn, n, to):
    """Both checks in one store call, so nothing else can slip in between "the target is free" and the move itself."""
    row = active_by_slot(conn, n - 1)
    if row is None:
        return f"no item #{n}"
    if active_by_slot(conn, to - 1) is not None:
        return f"#{to} is already taken"
    item_id, text, _ = row
    return item_id, text


def _set_slot(conn, item_id, slot):
    conn.execute("UPDATE items SET slot = ? WHERE id = ?", (slot, item_id))
    conn.commit()


async def move_item(ctx, n, to):
    if not (1 <= to <= MAX_SLOTS):
        await ctx.reply(f"slot must be 1-{MAX_SLOTS}")
        return
    result = await bot.store.run(_check_move, n, to)
    if isinstance(result, str):
        await ctx.reply(result)
        return
    item_id, text = result
    await canvas.move(item_id, x=BOARD_X, y=slot_y(to - 1), w=NOTE_W, h=NOTE_H)
    await bot.store.run(_set_slot, item_id, to - 1)
    await ctx.reply(f"moved #{n} to #{to}: {text}")


async def list_items(ctx):
    rows = await bot.store.run(active_items)
    if not rows:
        await ctx.reply("the board is empty")
        return
    lines = []
    for _, slot, text, _, added_by in rows:
        name = await name_of(added_by) if added_by else None
        lines.append(f"#{slot + 1}: {text} (added by {name})" if name else f"#{slot + 1}: {text}")
    await ctx.reply("\n".join(lines))


@bot.command(name="board", help="List, `add <text>`, `done <n>`, `move <n> <slot>`, or `clear` (asks to confirm)", usage="[add|done|move|clear|list ...]")
async def board_cmd(ctx, sub: str = None, rest: str = None):
    sub = (sub or "list").lower()
    if sub == "list":
        await list_items(ctx)
    elif sub == "add" and rest:
        await add_item(ctx, rest)
    elif sub == "done" and rest and rest.isdigit():
        await done_item(ctx, int(rest))
    elif sub == "move":
        parts = (rest or "").split()
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            await move_item(ctx, int(parts[0]), int(parts[1]))
        else:
            await ctx.reply(HELP_TEXT)
    elif sub == "clear":
        await clear_board(ctx)
    else:
        await ctx.reply(HELP_TEXT)


def _txn_deactivate_matching(conn, ids):
    for item_id, _, _, _, _ in active_items(conn):
        if item_id in ids:
            conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()


@bot.event
async def on_canvas_objects_removed(frame):
    await bot.store.run(_txn_deactivate_matching, set(frame.get("object_ids") or []))


def _txn_deactivate_before_seq(conn, before_seq):
    for item_id, _, _, seq, _ in active_items(conn):
        if before_seq is not None and seq <= before_seq:
            conn.execute("UPDATE items SET active = 0 WHERE id = ?", (item_id,))
    conn.commit()


@bot.event
async def on_canvas_cleared(frame):
    await bot.store.run(_txn_deactivate_before_seq, frame.get("before_seq"))


@bot.event
async def on_connect():
    global canvas
    canvas = Canvas(bot.client, bot.channel)
    await reconcile()


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
