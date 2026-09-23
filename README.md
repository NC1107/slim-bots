# slim-bots

Bot templates for [slim-m](https://github.com/NC1107/slim-m).

Each directory is a working bot in one file, meant to be copied rather than
imported. There is no library here on purpose: a bot is an ordinary program
holding a token, and the whole surface it needs is documented in slim-m's
[`docs/bots/building-bots.md`](https://github.com/NC1107/slim-m/blob/main/docs/bots/building-bots.md).

Read that first. Then pick the template closest to what you want.

## The templates

| Directory | What it is for |
| --- | --- |
| [`bot-ping`](bot-ping/) | The smallest thing that connects and answers. Start here. |
| [`bot-reminders`](bot-reminders/) | Durable state, a sync cursor, and exponential backoff. |
| [`bot-roles`](bot-roles/) | Self-service roles driven by a command, because reactor identity never reaches the wire. |
| [`bot-canvas-board`](bot-canvas-board/) | Driving the Voice Canvas from outside the app. |
| [`bot-modlog`](bot-modlog/) | Watching moderation events, and what is missing when one is dropped. |

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
