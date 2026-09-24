# bot-reminders

A slim-m bot: `!remind me in 2h <text>`, `!remind me at 15:30 <text>`,
`!remind me every monday [at 09:00] <text>`, `!remind me every 2h <text>`,
and `!reminders` to list, cancel, edit, or snooze your own. `!timezone
<IANA name>` sets the zone `at`/`every ... at` and the listing use.

Unlike `bot-ping`, this template is built on the `slimbots` package in
`../slimbots/`, which covers the plumbing every template but `bot-ping`
shares - auth, the REST call, retries, the websocket handshake, and the
reconnect loop. `bot-ping` stays free of it on purpose; see its own README.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid>,<channel-uuid> \
python3 bot.py
```

`SLIMM_CHANNELS` is a comma-separated list of channel ids. The bot only
watches and answers in those channels; see "Getting a token" and "What your
bot may do" in `docs/bots/building-bots.md` for how to find channel ids and
grant the bot `SEND_MESSAGES`/`VIEW_CHANNEL` there.

State (pending reminders, each member's timezone, and each channel's sync
cursor) lives in a sqlite file next to the script, `reminders.db` by default
(`SLIMM_DB_PATH` to move it). Restarting the bot does not lose a reminder
that has not fired yet, and an older database file picks up the new columns
automatically (`_ensure_column` in `bot.py`) rather than needing a fresh one.

`test_bot.py` exercises the commands below - including recurrence, editing,
snoozing, timezones, and the safeguards - against `slimbots.testing.FakeClient`,
with no live deployment and no socket: `python3 test_bot.py` runs it
directly. See `slimbots/README.md`'s "Testing a bot built on this."

## Commands

- `!remind me in <duration> <text>` - a one-off, relative to now (`2h`,
  `90s`, `1h30m`).
- `!remind me at <HH:MM> <text>` - a one-off, at that clock time in your own
  timezone (`!timezone` to set one; UTC otherwise).
- `!remind me every <weekday> [at <HH:MM>] <text>` - recurring weekly, in
  your own timezone, defaulting to 09:00 if no time is given.
- `!remind me every <duration> <text>` - recurring at a fixed interval, no
  tighter than `MIN_RECUR_SECONDS`.
- `!reminders` - list your own pending reminders here, in due order.
- `!reminders cancel <n>` - cancel the nth one from that list.
- `!reminders edit <n> <new text>` - change its text without cancelling and
  re-creating it.
- `!reminders snooze <n> <duration>` - push its next fire time back.
- `!timezone <IANA name>` - set your own timezone (`America/New_York`,
  `Europe/London`, ...); `!timezone` alone shows what is set.

Commands are rate-limited per person (a burst allowance, not a
reminder-setting-speed cap - see "Safeguards" below).

## Recurring reminders

A recurring reminder's row is never re-created when it fires - its own
`due_at` is recomputed in place and the row is kept `sent = 0`, which is
what lets `!reminders`, `cancel`, `edit`, and `snooze` keep working on it
exactly like a one-off, forever, until it is cancelled. See `recurrence.py`
for the two schedule kinds (`weekly`, `interval`) and the DST-safe weekly
recompute via `zoneinfo`.

Because the same row can now fire more than once, the delivered message's
idempotency key can no longer be the row's own id (that was only ever safe
because a one-off fires exactly once) - it is derived from
`(reminder_id, due_at)` instead, so a crash-and-retry of the *same* firing
still cannot double-post, while the next occurrence gets a fresh id with no
extra state needed to track which id was used last.

## Timezones

`zoneinfo` (standard library since Python 3.9) does the timezone math; the
one thing it does not guarantee is an installed IANA database, which a
minimal container image may not have. `tzdata` on PyPI is the fallback
`zoneinfo` reaches for automatically when the OS has none - the one
dependency this template adds over the others, and the reason it is in
`requirements.txt`.

## Safeguards

- **Bot and webhook authors are always ignored**, not just the bot's own
  messages - `slimbots.AuthorFilter`. This closes the misfire this repo
  actually hit from the other side: this bot posting `reminder: !daily`
  must never be read as a `!daily` command by anything else reading the
  channel, bot or human.
- **Delivered reminder text is always wrapped in a code span** (backticks
  in the text itself are escaped first, so it cannot break out of the
  span). A person can set a reminder whose text is a bot command on
  purpose or by accident; when it fires, it reads as quoted text, not as a
  command, to any bot watching the channel - a second, independent layer
  under the author check above, since a bot with looser matching than
  `AuthorFilter` might not check authorship at all.
- **A cap on pending reminders per person, per channel**
  (`MAX_PENDING_PER_USER`) - the guard against "a person setting ten
  thousand reminders." A straight `COUNT(*)` against the durable table, not
  an in-memory quota that would need reconciling with reality after a
  restart.
- **A minimum recurring interval** (`MIN_RECUR_SECONDS`) - the guard
  against "a one-second recurring reminder." A weekly recurrence has no
  such floor to hit; it fires at most once a week by construction.
- **A length bound on reminder text** (`MAX_TEXT_LEN`,
  `slimbots.limits.require_len`).
- **A per-user command rate limit**, same shape as bot-casino's.
- **Old, resolved reminders are pruned** on an hourly sweep - sent or
  cancelled rows older than 30 days. A pending reminder is never touched by
  this, however old; only ones already done with.
- **Graceful shutdown and connection isolation** - `slimbots.lifecycle`.
  SIGTERM (docker sends it on every redeploy) exits cleanly, and a single
  bad message is caught and logged rather than tearing down the whole
  websocket connection.

## Output and embeds

Replies are plain text, formatted with markdown - a code span for reminder
text (see "Safeguards" above), multi-line listings for `!reminders`. The
seam for a future embeds-aware version is the same shape as bot-casino's:
every reply is built as one string (`f"..."`, or the `"\n".join(lines)`
pattern in `_reply_with_pending`) immediately before the single
`client.send(...)` call that ships it. Swapping that string-building step
for an embed payload, per handler, needs no change to the parsing or
scheduling logic above it.

## What this deliberately does not do

- **Sub-minute or sub-weekly-once recurrence.** `every <weekday>` fires
  once a week; a tighter cadence wants `every <duration>` instead, itself
  bounded below by `MIN_RECUR_SECONDS` - see "Safeguards."
- **Recovering a very long outage exactly.** The cursor covers ordinary
  reconnects. If a channel's cursor falls outside what `/sync` can answer
  (`reset: true`), this bot re-baselines at the channel's current head
  rather than trying to recover the exact gap - the same tradeoff slim-m's
  own reactions and pins accept (decision 0009). A `!remind` sent inside
  that specific window is the one case this bot can miss.
- **Answering outside `SLIMM_CHANNELS`.** Every channel the bot's own role
  can see is not automatically one it answers in.
- **A leap-second-exact weekly fire across a DST transition.**
  `recurrence.next_weekly` recomputes the next occurrence in the reminder's
  own stored timezone via `zoneinfo`, which handles the one-hour jump
  correctly; it is not attempting second-level precision through it, and no
  reminder bot needs to be.
