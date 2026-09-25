# slim-bots

Bot templates for [slim-m](https://github.com/NC1107/slim-m).

Each directory is a working bot, meant to be copied rather than imported - a
bot is an ordinary program holding a token, not something this repo runs
for you. The protocol underneath all of it is documented in slim-m's
[`docs/bots/building-bots.md`](https://github.com/NC1107/slim-m/blob/main/docs/bots/building-bots.md).

Read that first. Then pick the template closest to what you want.

## The library

[`slimbots/`](slimbots/) is a discord.py-shaped framework: a `Bot()` constructor, `@bot.command` with typed argument conversion, `ctx`, and a live `Space`/`Canvas` model (members/channels/roles, a channel's Voice Canvas).
See [`docs/framework.md`](docs/framework.md) for the whole shape, the built-in safeguards (bot-ignore, cooldowns, permission gates, lifecycle), and how config ownership, identity, command registration, and embeds work.
Every template here but `bot-ping` is built on it.

`bot-ping` stays free of it on purpose: it is the one template that shows
the whole protocol in a single file with nothing hidden behind an import.
If a future template looks inconsistent with that split, the split is
deliberate; see `bot-ping`'s own README before "fixing" it.

## The templates

| Directory | What it is for |
| --- | --- |
| [`bot-ping`](bot-ping/) | The smallest thing that connects and answers. Start here. |
| [`bot-greeter`](bot-greeter/) | Posts a welcome message when someone joins, the worked example for `on_member_join`. |
| [`bot-reminders`](bot-reminders/) | Durable state, recurring reminders, timezones, and a persisted cursor. |
| [`bot-roles`](bot-roles/) | Self-service roles driven by a command, with a real permission-gap diagnosis. |
| [`bot-canvas-board`](bot-canvas-board/) | Driving the Voice Canvas from outside the app. |
| [`bot-modlog`](bot-modlog/) | Watching moderation events, and what is missing when one is dropped. |
| [`bot-jellyfin`](bot-jellyfin/) | Polling an outside service (Jellyfin) instead of slim-m's own events, batching a library scan into one message instead of forty, and answering `!jellyfin search`/`recent`. |
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

[PolyForm Noncommercial 1.0.0](LICENSES/LicenseRef-PolyForm-Noncommercial-1.0.0.txt), the same terms as slim-m.
The templates are free to copy and adapt under those terms - noncommercial use, not "copy freely" without qualification.
