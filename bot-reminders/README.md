# bot-reminders

A slim-m bot in one file: `!remind me in 2h <text>`, `!remind me at 15:30 <text>`,
and `!reminders` to list or cancel your own.

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

State (pending reminders and each channel's sync cursor) lives in a sqlite
file next to the script, `reminders.db` by default (`SLIMM_DB_PATH` to move
it). Restarting the bot does not lose a reminder that has not fired yet.

## What this deliberately does not do

- **Recurring reminders** (`every monday`). Left out of this example on
  purpose: it needs a real schedule grammar and a recompute-on-fire step, and
  would roughly double the file for a feature the base cases do not need to
  demonstrate. A fork wanting it should add a `recur_rule` column and, on
  send, insert the next occurrence instead of leaving the row `sent`.
- **Time zones.** `at HH:MM` is always UTC. A bot that must honor a member's
  own local time needs to ask for one and store it per user.
- **Editing a reminder.** Cancel it (`!reminders cancel <n>`) and set a new
  one.
- **Reconstructing exactly what happened during a very long outage.** The
  cursor covers ordinary reconnects. If a channel's cursor falls outside what
  `/sync` can answer (`reset: true`), this bot re-baselines at the channel's
  current head rather than trying to recover the exact gap - the same
  tradeoff slim-m's own reactions and pins accept (decision 0009). A
  `!remind` sent inside that specific window is the one case this bot can
  miss.
- **Answering outside `SLIMM_CHANNELS`.** Every channel the bot's own role
  can see is not automatically one it answers in.
