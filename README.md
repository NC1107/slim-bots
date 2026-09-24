# slim-bots

Bot templates for [slim-m](https://github.com/NC1107/slim-m).

Each directory is a working bot in one file, meant to be copied rather than
imported. There is no library here on purpose: a bot is an ordinary program
holding a token, and the whole surface it needs is documented in slim-m's
[`docs/bots/building-bots.md`](https://github.com/NC1107/slim-m/blob/main/docs/bots/building-bots.md).

Read that first. Then pick the template closest to what you want.

## The library

[`slimbots/`](slimbots/) is a discord.py-shaped framework: a `Bot()` constructor, `@bot.command` with typed argument conversion, `ctx`, and a live `Space` model (members/channels/roles).
See [`docs/framework.md`](docs/framework.md) for the whole shape, the built-in safeguards (bot-ignore, cooldowns, permission gates, lifecycle), and how identity, command registration, and the embed seam work.
`bot-casino` is built on it and is the reference for a real port.

The pre-0.3 sync primitives - auth, the REST call, the websocket handshake, the reconnect loop, the `seq`-cursor/`/sync` helpers - are still there and still used by every template not yet ported to `Bot`.
`slimbots.testing` has `FakeClient` (sync) and `FakeAsyncClient` (for `Bot`), for unit-testing a bot's command handlers with no live deployment.

`bot-ping` stays free of it on purpose: it is the one template that shows
the whole protocol in a single file with nothing hidden behind an import.
Every other template here is built on it. If a future template looks
inconsistent with that split, the split is deliberate; see `bot-ping`'s own
README before "fixing" it.

## The templates

| Directory | What it is for |
| --- | --- |
| [`bot-ping`](bot-ping/) | The smallest thing that connects and answers. Start here. |
| [`bot-reminders`](bot-reminders/) | Durable state, a sync cursor, and exponential backoff. |
| [`bot-roles`](bot-roles/) | Self-service roles driven by a command, because reactor identity never reaches the wire. |
| [`bot-canvas-board`](bot-canvas-board/) | Driving the Voice Canvas from outside the app. |
| [`bot-modlog`](bot-modlog/) | Watching moderation events, and what is missing when one is dropped. |
| [`bot-jellyfin`](bot-jellyfin/) | Polling an outside service (Jellyfin) instead of slim-m's own events, and batching a library scan into one message instead of forty. |
| [`bot-casino`](bot-casino/) | A currency bot: daily chips, blackjack (double, split, surrender), coinflip, transfers, a leaderboard, and money kept safe under real concurrency. |

Every template's README says what it deliberately does not do. That section is
usually the more useful half.

## Running one

Each directory has its own `requirements.txt` and its own README, but the shape
is the same:

```bash
cd bot-ping
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
