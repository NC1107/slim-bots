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

## What it does anyway, because these are not teaching points

It never answers another bot or a webhook - `GET /users/{id}`'s
`is_bot`/`is_webhook` fields are checked alongside the usual "is this me"
author check, cached per id - and it exits 0
on SIGTERM instead of however an unhandled signal would end it. Both are a
handful of lines kept inline rather than pulled from `slimbots`, for the same
reason the rest of this file stays library-free: see the module docstring.

## What this deliberately does not do

See the module docstring in `bot.py`: no cursor, a flat reconnect delay
instead of backoff, and it answers in any channel it can see rather than
being scoped to one. `bot-reminders`, `bot-roles` and `bot-canvas-board`
each turn one or more of those into a real feature.

## Tests

`python3 -m unittest test_bot.py` - stdlib only, no live deployment. It
covers `should_answer`'s three refusals (self, another bot, not a trigger)
by monkeypatching `bot.call` rather than hitting a real server.
