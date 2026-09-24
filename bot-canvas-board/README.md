# bot-canvas-board

A slim-m bot that keeps a todo board on a voice channel's Voice Canvas:
`!board add <text>` places a sticky note, `!board done <n>` removes it,
`!board move <n> <slot>` repositions it, `!board clear` (asks to confirm)
wipes the whole board, and `!board` (or `!board list`) shows what is up
and which canvas it is drawing on, crediting whoever added each note.

Built on the `slimbots` `Bot` framework - see `../docs/framework.md`. The
Voice Canvas calls go through the `Canvas` model
(`canvas.place`/`move`/`remove`/`viewport`) instead of building the
`canvas/objects`/`canvas/ops` request bodies by hand, and the three canvas
events map onto `on_canvas_object_placed`/`on_canvas_objects_removed`/
`on_canvas_cleared`. `Bot` owns `SLIMM_URL`/`SLIMM_BOT_TOKEN`/
`SLIMM_CHANNELS` and the seq cursor for the channel's message traffic; this
script only opens its own tables at `bot.data_path` (from `SLIMM_DB_PATH`),
never importing `os` itself. `bot-ping` stays free of the library on
purpose; see its own README.

## Commands in a text channel, drawing on a voice channel

Since 0.3.1, `!board` commands can come from an ordinary text channel while
the board itself lives on a separate voice channel's canvas - `CANVAS_CHANNEL`
names which one:

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<text-channel-uuid>,<voice-channel-uuid> \
CANVAS_CHANNEL=<voice-channel-uuid> \
python3 bot.py
```

`SLIMM_CHANNELS` must include *both* the text channel(s) `!board` is typed
in and the voice channel `CANVAS_CHANNEL` names - the voice channel needs
to be watched too, or its `canvas.object.placed`/`canvas.objects.removed`/
`canvas.cleared` events never reach this bot and reconciliation only
catches up on the next reconnect instead of live. `CANVAS_CHANNEL` accepts
either the channel id or its name. The bot refuses to start if it does not
name a real, visible, voice channel, or if that channel is missing from
`SLIMM_CHANNELS`.

**Backward compatible:** if `CANVAS_CHANNEL` is not set and `SLIMM_CHANNELS`
names exactly one channel, this bot behaves exactly like before 0.3.1 -
that one channel is both where commands are typed and where the board is
drawn - logging a startup warning so the gap is visible without breaking
an existing deployment mid-upgrade. If `SLIMM_CHANNELS` names more than one
channel and `CANVAS_CHANNEL` is unset, it refuses to start rather than
guess which one is the canvas.

The bot needs `SEND_MESSAGES`, `VIEW_CHANNEL` and `USE_CANVAS` on the
voice channel - no `MANAGE_CANVAS`, since it only ever places, moves and
removes objects it authored itself - and `VIEW_CHANNEL`/`SEND_MESSAGES` on
whichever text channel(s) it takes commands from. See "Getting a token"
and "What your bot may do" in `docs/bots/building-bots.md` for how to find
a channel id and grant those.

State (which canvas object is in which of the board's 20 slots, each one's
own `seq`, and - shared with `Bot` - the seq cursor for the channel's own
message traffic) lives in a sqlite file next to the script, `board.db` by
default (`SLIMM_DB_PATH` to move it).

## What this proves that the other examples do not

This bot is the one example that writes to the Voice Canvas:
`canvas.place` (`POST .../canvas/objects`), `canvas.move`/`canvas.remove`
(`POST .../canvas/ops`), `canvas.viewport` (`GET .../canvas/objects` to
read the board back), and the three canvas events on the wire
(`canvas.object.placed` is received but not acted on; `.removed` and
`canvas.cleared` are).

Two things that turned out to matter:

- **A note's text cannot be edited**, ever - only its position can. There is
  no route for it. `!board move` exists because a position change has one;
  an `!board edit` command does not, because it would need one that is not
  there.
- **A bulk `canvas.cleared` frame carries no object ids**, on purpose - a
  clear can wipe a channel's whole live ceiling and the broadcast frame is
  bounded. Telling a clear apart from noise requires this bot to have kept
  each of its own notes' `seq` from the moment it placed them, so it can
  compare that against the clear's `before_seq` itself; nothing on the wire
  will do that comparison for it.

## Safeguards

- **A length bound on note text** (`MAX_TEXT_LENGTH`), before it ever
  reaches the canvas API - not a platform limit, just this bot declining to
  place something a fixed 220x140 box could never show usefully.
- **A confirmation on `!board clear`.** It asks how many items it would
  remove and waits for a yes/no reply (`ctx.confirm`, a 30-second timeout
  that answers no) rather than removing anything on the bare command.
- **A usage hint instead of silence.** Anything starting with `!board` that
  does not match a known subcommand gets `HELP_TEXT` back rather than
  nothing at all.
- **It never answers another bot** - `Bot`'s own bot-ignore default.

## What this deliberately does not do

- **A note a human places inside the board's rectangle.** Reconciliation
  only ever looks at objects this bot's own id authored; anything else
  sharing that patch of canvas is left alone, including by the free-slot
  search, which does not know it is there.
- **Restoring an undone remove or clear.** `canvas.objects.restored` is not
  handled. A moderator's undo brings a note back for everyone who can see
  the canvas; this bot's own idea of the board does not follow it back.
- **More than 20 items, or more than one board per channel.** `!board add`
  refuses once every slot is full rather than growing the column - an
  unbounded column would need an unbounded viewport query to reconcile.
- **Moving a note to an arbitrary canvas position.** `!board move` only
  retargets within the board's own slot column, never to a free-form `x,y`.
  A note moved outside that rectangle would fall outside the query this bot
  re-reads on every reconnect, and would then read as removed rather than as
  moved - the same "bounded query, bounded truth" tradeoff that makes
  reconciling `canvas.cleared` possible at all.
