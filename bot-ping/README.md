# bot-ping

The smallest slim-m bot: it answers `!ping` with `pong`, in one file.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... python3 bot.py
```

## What it demonstrates

The whole shape of a bot, and nothing else:

1. It authenticates with `Authorization: Bearer <token>`, like any client.
2. It mints a single-use ticket from `POST /auth/ws-ticket`.
3. It opens the WebSocket, says hello with that ticket, and reads events.
4. It answers with an ordinary `POST` to the messages route.

There is no bot-specific protocol anywhere in that list.
A bot is a member with a long-lived credential, so every call here is a call a person's client makes too - which is the point of decision 0028 and the reason this file is short.

A frame type it does not recognise is ignored rather than treated as an error.
New event types get added over time, and a bot that dies on one it has never heard of breaks on somebody else's upgrade.

## Deliberately library-free

Every other template in this repo is built on the `slimbots` `Bot` framework in `../slimbots/`, which covers the plumbing they all share - see `../docs/framework.md`.
This one is not, on purpose.
It is meant to be the file that shows the whole protocol with nothing hidden behind an import, so it is still the right place to read first even after the rest of the repo grew a framework.
If a future change makes this one look inconsistent with the others, that inconsistency is the point - please do not "fix" it by making this one depend on `slimbots` too.

## Two things that are not corners cut

A bot answering `!ping` forever, or dying ugly on a container restart, is not a teaching point, it is a bug - so these two are handled in full, inline, rather than imported from `slimbots`:

- **It never answers another bot or webhook.** `should_answer` checks the author against its own id, and against `GET /users/{id}`'s `is_bot`/`is_webhook` fields (cached per id). Several bots sharing one channel is exactly the setup where answering another bot's own output starts an infinite loop.
- **SIGTERM exits cleanly.** A container orchestrator stops a bot with SIGTERM, not by pulling the plug; this logs why, then exits 0.

## Deliberately minimal

These are the corners it does cut, each turned into a real feature by another template:

- **No cursor.** It only sees what arrives while connected, not what happened while it was down - `bot-reminders` and every other framework-based template get this for free from `Bot`'s own cursor.
- **A flat reconnect delay** instead of exponential backoff.
- **Answers every channel it can see** instead of being scoped to one - `Bot(channels=...)` handles that everywhere else.
