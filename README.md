# slim-bots

Bot templates for [slim-m](https://github.com/Slim-m-org/slim-m).

Each directory is a working bot, meant to be copied rather than imported - a
bot is an ordinary program holding a token, not something this repo runs
for you. The protocol underneath all of it is documented in slim-m's
[`docs/bots/building-bots.md`](https://github.com/Slim-m-org/slim-m/blob/main/docs/bots/building-bots.md).

Read that first. Then pick the template closest to what you want.

## Not a marketplace

This is a set of examples the project maintains, and nothing more.
There is no registry, no rating, no install step and no list that means "approved".
A bot here being listed says that it builds, that its tests pass and that the project keeps it working against slim-m, not that it is safe to hand a token to.

A bot is a program on your machine holding a long-lived credential in your Space.
The code you read is not guaranteed to be the code you run, so read it, pin what you run, and decide for yourself.
That is also why nothing installs a bot for you: you clone one and run it.

New here? [`docs/getting-started.md`](docs/getting-started.md) goes from wanting a bot to having one running, and [`bots/_template`](bots/_template/) is the copy-me starting point.

## The library

[`slimbots/`](slimbots/) is a discord.py-shaped framework: a `Bot()` constructor, `@bot.command` with typed argument conversion, `ctx`, and a live `Space`/`Canvas` model (members/channels/roles, a channel's Voice Canvas).
See [`docs/framework.md`](docs/framework.md) for the whole shape, the built-in safeguards (bot-ignore, cooldowns, permission gates, lifecycle), and how config ownership, identity, command registration, and embeds work.
Every template here but `bots/ping` is built on it.

`bots/ping` stays free of it on purpose: it is the one template that shows
the whole protocol in a single file with nothing hidden behind an import.
If a future template looks inconsistent with that split, the split is
deliberate; see `bots/ping`'s own README before "fixing" it.

## The templates

All of them live under [`bots/`](bots/).

| Directory | What it is for |
| --- | --- |
| [`bots/_template`](bots/_template/) | Copy this to start a bot: a command, a button, a private reply and durable state, with tests that need no server. |
| [`bots/ping`](bots/ping/) | The smallest thing that connects and answers. Start here. |
| [`bots/greeter`](bots/greeter/) | Posts a welcome message when someone joins, the worked example for `on_member_join`. |
| [`bots/reminders`](bots/reminders/) | Durable state, recurring reminders, timezones, and a persisted cursor. |
| [`bots/roles`](bots/roles/) | Self-service roles driven by a command, with a real permission-gap diagnosis. |
| [`bots/canvas-board`](bots/canvas-board/) | Driving the Voice Canvas from outside the app. |
| [`bots/modlog`](bots/modlog/) | Watching moderation events, and what is missing when one is dropped. |
| [`bots/jellyfin`](bots/jellyfin/) | Polling an outside service (Jellyfin) instead of slim-m's own events, batching a library scan into one message instead of forty, and answering `!jellyfin search`/`recent`. |
| [`bots/casino`](bots/casino/) | A currency bot: daily chips, blackjack (double, split, surrender), coinflip, transfers, a leaderboard, and money kept safe under real concurrency. |
| [`bots/pelican`](bots/pelican/) | Watching and controlling an outside game-server panel (Pelican): a `!servers` status list, a status message edited in place, and role-gated, audit-logged power commands. |
| [`bots/automod`](bots/automod/) | Moderator-written rules (flood, links, words, mention spam) that delete and time out, log what they did, and stay off until configured. |
| [`bots/music`](bots/music/) | Playing audio into a voice call: a per-call queue fed from a Jellyfin music library or a direct stream URL, published as a microphone track. |
| [`bots/sonarr`](bots/sonarr/) | Announcing grabbed, downloaded, upgraded and failed episodes from Sonarr's history with per-episode dedupe, plus `!sonarr search`/`add`/`queue`/`calendar` and a button chooser. |
| [`bots/radarr`](bots/radarr/) | The same for movies from Radarr, deduped by TMDB id so a replaced file never reposts. |
| [`bots/seerr`](bots/seerr/) | A request flow for Seerr: `!request` with a button chooser, announcements of requests and availability, and Approve/Decline buttons gated on a slim-m permission. |
| [`bots/starboard`](bots/starboard/) | Reposting a well-reacted message to a highlights channel, keeping it in sync as the count and the original change, and a weekly digest. |
| [`bots/github-releases`](bots/github-releases/) | Polling an outside service with an ETag, rate-limit backoff and a first-run-posts-nothing rule, to post each new GitHub release with its notes condensed to a short list. |

Every template's README says what it deliberately does not do. That section is
usually the more useful half.

## Running one

Each directory has its own `requirements.txt` and its own README, but the shape
is the same:

```bash
cd bots/ping
pip install -r requirements.txt
SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... python3 bot.py
```

A bot token is minted by an admin in the Bots section of Space settings, is
shown once, and does not rotate. A `401` is terminal: it means the token was
revoked, so exit rather than retrying.

## Shipping a bot

Every bot PR follows [`docs/shipping-a-bot.md`](docs/shipping-a-bot.md), which also lists what CI checks for you: tests, an in-app test in a private channel (`scripts/smoke_bot.py`), review and adversarial passes, a UI pass, and a rollout step.

## Conventions

These are the same rules slim-m itself uses.
CI enforces the first two through `scripts/check_hygiene.py` (run it locally), and docstrings are capped at two lines by the `slimbots` suite:

- plain `#` comments are one line; put longer reasoning in a docstring or the README
- no em dash, and no emoji (a reaction emoji in data is written as a `\u` escape)
- Python 3 standard library where it will do the job

## Licence

[PolyForm Noncommercial 1.0.0](LICENSES/LicenseRef-PolyForm-Noncommercial-1.0.0.txt), the same terms as slim-m.
The templates are free to copy and adapt under those terms - noncommercial use, not "copy freely" without qualification.
