# slim-bots

Bot templates for [slim-m](https://github.com/NC1107/slim-m), plus a small
shared library, `slimbots/`, for the plumbing every template but one repeats.

A bot is an ordinary program holding a token, and the whole surface it needs
is documented in slim-m's
[`docs/bots/building-bots.md`](https://github.com/NC1107/slim-m/blob/main/docs/bots/building-bots.md).
Read that first. Then pick the template closest to what you want.

## The library

[`slimbots/`](slimbots/) covers auth, the REST call, an idempotent `send`
with retries, the websocket handshake, the reconnect loop with backoff, and
the `seq`-cursor/`/sync` helpers. It is not a typed model of slim-m's API -
no `Channel` or `Message` objects, no cache - because slim-m's bot surface is
small enough that this is the whole plumbing layer needed. `slimbots.testing`
also has `FakeClient`, for unit-testing a bot's command handlers with no live
deployment - `bot-reminders/test_bot.py` is a worked example. See its own
README for what it covers and why it is hand-written rather than generated.

`bot-ping` stays free of it on purpose: it is the one template that shows
the whole protocol in a single file with nothing hidden behind an import.
Every other template here is built on it. If a future template looks
inconsistent with that split, the split is deliberate; see `bot-ping`'s own
README before "fixing" it.

## The templates

| Directory | What it is for |
| --- | --- |
| [`bot-ping`](bot-ping/) | The smallest thing that connects and answers. Start here. Library-free on purpose. |
| [`bot-reminders`](bot-reminders/) | Durable state, a sync cursor, and exponential backoff. |
| [`bot-roles`](bot-roles/) | Self-service roles driven by a command, because reactor identity never reaches the wire. |
| [`bot-canvas-board`](bot-canvas-board/) | Driving the Voice Canvas from outside the app. |
| [`bot-modlog`](bot-modlog/) | Watching moderation events, and what is missing when one is dropped. |
| [`bot-webhook-relay`](bot-webhook-relay/) | A tiny HTTP server that turns a POSTed JSON body into a message, for tools that only speak "webhook" - a stopgap for slim-m's own incoming webhooks (decision 0030), not built yet. |

Every template's README says what it deliberately does not do. That section is
usually the more useful half.

## Running one

Each directory has its own `requirements.txt` and its own README. Every
template but `bot-ping` depends on `slimbots`, installed straight from this
repo:

```bash
cd bot-reminders
pip install -r requirements.txt
SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... python3 bot.py
```

A bot token is minted by an admin in the Bots section of Space settings, is
shown once, and does not rotate. A `401` is terminal: it means the token was
revoked, so exit rather than retrying.

## Conventions

These are the same rules slim-m itself uses, and CI is not enforcing them here
yet, so they are on you:

- plain `#` comments are one line; put longer reasoning in a docstring or the README
- no em dash, and no emoji
- Python 3 standard library where it will do the job

## Licence

Same terms as slim-m. Copy freely.
