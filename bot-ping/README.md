# bot-ping

The smallest slim-m bot: it answers `!ping` with `pong`, in one file.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... python3 bot.py
```

## Deliberately library-free

Every other template in this repo (`bot-reminders`, `bot-roles`,
`bot-canvas-board`, `bot-modlog`) is built on the `slimbots` package in
`../slimbots/`, which covers the plumbing they all share: auth, the REST
call, the websocket handshake, the reconnect loop, retries, and the `seq`
cursor. This one is not, on purpose. It is meant to be the file that shows
the whole protocol with nothing hidden behind an import, so it is still the
right place to read first even after the rest of the repo grew a library.
If a future change makes this one look inconsistent with the others, that
inconsistency is the point - please do not "fix" it by making this one
depend on `slimbots` too.

## What this deliberately does not do

See the module docstring in `bot.py`: no cursor, a flat reconnect delay
instead of backoff, and it answers in any channel it can see rather than
being scoped to one. `bot-reminders`, `bot-roles` and `bot-canvas-board`
each turn one or more of those into a real feature.
