# bot-reminders

A slim-m bot: `!remind in 2h <text>`, `!remind at 15:30 <text>`,
`!remind every monday [at 09:00] <text>` or `!remind every 2h <text>`
for a recurring one, and `!reminders` to list, or `cancel`/`edit`/`snooze`
your own by its listed number. `!timezone <IANA name>` sets the zone
`at`/`every ... at` and the listing are shown in.

Built on the `slimbots` `Bot` framework - see `../docs/framework.md`.
`Bot` owns `SLIMM_URL`/`SLIMM_BOT_TOKEN`/`SLIMM_CHANNELS`, the seq cursor,
and `bot.data_path` (from `SLIMM_DB_PATH`, defaulting to `reminders.db`);
this script only opens its own tables there. `!remind` and `!reminders`
are each a `@bot.group()` with `in`/`at`/`every` and `cancel`/`edit`/`snooze`
as real `@group.command()` subcommands, using the framework's own
`Duration` (`10m`, `2h30m`) and `TimeOfDay` (`14:30`) argument types rather
than a trigger regex per shape. `bot-ping` stays free of the library on
purpose; see its own README.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid>,<channel-uuid> \
python3 bot.py
```

`SLIMM_CHANNELS` is a comma-separated list of channel ids; see "Getting a
token" and "What your bot may do" in `docs/bots/building-bots.md` for how
to find channel ids and grant the bot `SEND_MESSAGES`/`VIEW_CHANNEL` there.

State (pending reminders, per-user timezones, and - shared with `Bot` -
the seq cursor) lives in a sqlite file next to the script, `reminders.db`
by default (`SLIMM_DB_PATH` to move it). Restarting the bot does not lose
a reminder that has not fired yet, recurring or not.

## Recurring reminders

A recurring reminder's row is never re-created when it fires: its own
`due_at` is recomputed in place and the row kept, which is what lets
`!reminders`/`cancel`/`edit`/`snooze` keep working on it exactly like a
one-off. Because the same row can now fire more than once, the delivered
message's idempotency key is derived from `(reminder_id, due_at)` instead
of the row's own id - a crash-and-retry of the *same* firing still cannot
double-post, while the *next* occurrence gets a fresh id. See
`recurrence.py` for the weekly/interval scheduling math, including the
DST-safe weekly recomputation via `zoneinfo`.

## Safeguards

- **A per-user command rate limit** (a burst allowance, not a play-speed
  cap) via `bot.check`.
- **A pending-reminder cap per person per channel** (`MAX_PENDING_PER_USER`),
  against a reminder storm.
- **A floor on recurring intervals** (`MIN_RECUR_SECONDS`), against a typo
  like `every 1s`.
- **A length cap on reminder text** (`MAX_TEXT_LEN`).
- **Delivered text is always backtick-quoted.** This is the fix for a real
  misfire this repo hit: a reminder whose text was `!daily` used to make
  bot-casino credit the *reminders bot's own account*, because balances
  were keyed on whoever posted the message without checking whether that
  "whoever" was a program. `Bot`'s own bot-ignore default now closes that
  at the source; backtick-quoting is a second, independent layer, so even
  a bot with looser matching sees literal quoted text, never something
  that starts with `!`.
- **Pruning.** A resolved (sent or cancelled) reminder is deleted after
  `REMINDER_RETENTION_SECONDS`; a pending one never is.

## Output

A delivered reminder carries a small `Embed` (title "Reminder", a footer
naming the recurrence if any) alongside its existing backtick-quoted plain
text. See `../docs/framework.md`'s embeds section for the fallback an
older server gets instead.

## What this deliberately does not do

- **Reconstructing exactly what happened during a very long outage.** The
  cursor covers ordinary reconnects. If a channel's cursor falls outside
  what `/sync` can answer (`reset: true`), this bot re-baselines at the
  channel's current head rather than trying to recover the exact gap - the
  same tradeoff slim-m's own reactions and pins accept (decision 0009). A
  `!remind` sent inside that specific window is the one case this bot can
  miss.
- **Sub-minute recurrence, or recurring more than once a day by weekday.**
  `every <weekday>` fires once a week by design; a tighter cadence wants
  `every <duration>` instead, bounded below by `MIN_RECUR_SECONDS`.
- **Answering outside `SLIMM_CHANNELS`.** Every channel the bot's own role
  can see is not automatically one it answers in.
