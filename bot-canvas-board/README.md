# bot-canvas-board

A slim-m bot that keeps a todo board on a channel's Voice Canvas:
`!board add <text>` places a sticky note, `!board done <n>` removes it,
`!board move <n> <slot>` repositions it, `!board clear yes` wipes the whole
board, and `!board` (or `!board list`) shows what is up, crediting whoever
added each note. `!board help`, or anything starting with `!board` that
does not match a known command, gets a one-line usage reminder back instead
of silence.

Unlike `bot-ping`, this template is built on the `slimbots` package in
`../slimbots/`, which covers the plumbing every template but `bot-ping`
shares - auth, the REST call, retries, the websocket handshake, the
reconnect loop, and the sqlite `seq`-cursor helpers used for the channel's
message traffic. `bot-ping` stays free of it on purpose; see its own README.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNEL=<channel-uuid> \
python3 bot.py
```

The bot needs `SEND_MESSAGES`, `VIEW_CHANNEL` and `USE_CANVAS` on
`SLIMM_CHANNEL` - no `MANAGE_CANVAS`, since it only ever places, moves and
removes objects it authored itself. See "Getting a token" and "What your bot
may do" in `docs/bots/building-bots.md` for how to find the channel id and
grant those.

State (which canvas object is in which of the board's 20 slots, and each
one's own `seq`) lives in a sqlite file next to the script, `board.db` by
default (`SLIMM_DB_PATH` to move it).

## What this proves that the other examples do not

`bot-ping` and its descendants only ever call `POST .../messages`. This bot
is the first to write to the Voice Canvas: `POST .../canvas/objects` to
place a note, `POST .../canvas/ops` to move or remove one, `GET
.../canvas/objects` to read the board back, and the three canvas events on
the wire (`canvas.object.placed` is received but not acted on; `.removed`
and `canvas.cleared` are).

Two things that turned out to matter, both explained in the module
docstring in `bot.py`:

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
- **Everything `bot-reminders` already covers**: exponential backoff,
  channel scoping via `SLIMM_CHANNEL`, and a terminal 401 on a revoked
  token. See its own README.

## Safeguards

- **A length bound on note text** (`MAX_TEXT_LENGTH`, 240 characters): a note
  is a fixed 220x140 box, so text well past what that box could show is
  refused before it ever reaches the canvas API, not silently truncated.
- **A confirmation on `!board clear`.** Bare `!board clear` says how many
  items it would remove and does nothing; only `!board clear yes` actually
  removes them. Wiping every item on a shared board deserves more friction
  than any other command here.
- **It never answers another bot.** `slimbots.AuthorFilter` skips every
  automated author, not just itself, so several bots sharing a channel
  cannot loop through this one.

## Tests

`python3 test_bot.py` - stdlib only plus `slimbots.testing.FakeClient`, no
live deployment and no real canvas. Covers add/done/move/clear, the length
bound, the board-full and empty-board cases, the help fallback, and the
bot/self-ignore checks.
