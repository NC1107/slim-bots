# slimbots

A discord.py-shaped bot framework for [slim-m](https://github.com/NC1107/slim-m): a `Bot()` constructor, `@bot.command`, `ctx`, and a live `Space`/`Canvas` model, plus the safeguards seven real bots each used to rebuild by hand (bot-ignore, cooldowns, permission gates, clean lifecycle, a supervised background task).

```bash
pip install slim-m
```

The distribution is named `slim-m` on PyPI; the import stays `slimbots`. `bot-casino` in the [slim-bots](https://github.com/NC1107/slim-bots) repo is the reference port built on this; `docs/framework.md` there is the deeper reference for everything this page only shows the shape of.

## A minimal bot

```python
import os

from slimbots import Bot

bot = Bot(prefix="!")


@bot.command(help="Answer pong")
async def ping(ctx):
    await ctx.reply("pong")


if __name__ == "__main__":
    raise SystemExit(bot.run() or 0)
```

`Bot()` reads `SLIMM_URL`, `SLIMM_BOT_TOKEN`, and `SLIMM_CHANNELS` (comma-separated channel ids) itself - nothing above needs `import os` just to wire those up. Run it with:

```bash
SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... SLIMM_CHANNELS=<channel-uuid> python3 bot.py
```

A bot token is minted by an admin in the Bots section of Space settings, is shown once, and does not rotate. A `401` is terminal - `bot.run()` treats a revoked token as a reason to stop, not retry.

## Commands

`@bot.command` registers `async def name(ctx, *typed_args)`. Arguments convert from the function's own annotations:

```python
from slimbots import Member


@bot.command(help="Say hi to someone")
async def greet(ctx, member: Member, text: str = "hello"):
    await ctx.reply(f"{text}, {member.display_name}!")
```

- `int` / `float` convert one token, or reply with a clear `BadArgument` instead of a traceback.
- `str` is one token, unless it is the *last* parameter, in which case it consumes the rest of the message (`text` above).
- `Member` resolves `@username` or a bare username/id against `bot.space`.
- `Duration` (`10m`, `2h30m`) and `TimeOfDay` (`14:30`) convert a token into seconds or an hour/minute pair - built for exactly the reminder-bot shape.
- A parameter with a default is optional; a missing required one names itself in the reply.

`help` is generated automatically from whatever gets registered, unless a bot defines its own `help` command first.

## Command groups

A group dispatches its first argument token to a registered subcommand, the same shape `!remind in 10m <text>` needs:

```python
@bot.group(help="Schedule a reminder")
async def remind(ctx, rest: str = ""):
    await ctx.reply("try `!remind in <duration> <text>` or `!remind at <HH:MM> <text>`")


@remind.command(name="in", usage="<duration> <text>")
async def remind_in(ctx, duration, text: str):
    await ctx.reply(f"in {int(duration)}s: {text}")
```

An unrecognised or bare invocation falls back to the group's own function. `bot-reminders` in the templates repo is the worked example.

## Settings

`bot.setting(name, default=None, *, type=str, required=False)` reads one of a bot's *own* env vars the same way `Bot` reads its three, converting via `type` (`int`, `float`, or `list` for a comma-separated one):

```python
JELLYFIN_URL = bot.setting("JELLYFIN_URL", required=True)
JELLYFIN_POLL_SECONDS = bot.setting("JELLYFIN_POLL_SECONDS", 300, type=int)
```

A missing `required=True` value is never raised at the `setting()` call itself; it is collected and reported together with a missing `SLIMM_URL`/token/channels in one clear error when `bot.start()` runs. `Bot(default_data_path="mybot.db")` gives `bot.data_path` - the one place a bot's own sqlite file lives, derived from `SLIMM_DB_PATH` or that default.

## Background tasks

`bot.background(coro, name=None)` is the only way a bot should start a loop that outlives one command:

```python
@bot.event
async def on_ready():
    bot.background(poll_loop(), name="poll")


async def poll_loop():
    while True:
        await do_the_poll()
        await asyncio.sleep(300)
```

A plain `asyncio.create_task(...)` is only weakly referenced by the event loop and can be silently garbage-collected mid-run; `bot.background` holds a strong reference for as long as it runs. An unhandled exception in it (a `401` included) is treated as fatal: logged, the bot's main loop cancelled, and `bot.run()` exits non-zero so a container restarts it, instead of the loop's failure going unnoticed while the rest of the bot carries on.

## Embeds

```python
from slimbots import Embed

await ctx.reply(embed=Embed(title="Balance").add_field("chips", str(balance)))
```

`Embed` serializes to slim-m's real wire shape and sends for real; if an older server rejects the field as unknown, the framework retries once as the embed's own rendered markdown instead, so a bot degrades rather than breaking.

## The Space model

`bot.space` is `members`, `channels`, `roles` as live dicts, refreshed on connect:

```python
member = bot.space.get_member("nick")
await bot.space.grant_role(member, role)
```

`member.has_permission(permission)` checks the *caller's* base permissions - never the bot's own, which can hold anything a human member can, including `ADMINISTRATOR`.

## Testing

`slimbots.testing.FakeAsyncClient` never touches the network:

```python
from slimbots.testing import FakeAsyncClient

client = FakeAsyncClient(me_id="bot-1")
bot.client = client
await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!ping"})
assert client.sent[-1]["content"] == "pong"
```

Pre-stubbed routes (`/me`, `/channels`, `/members`, `/roles`, `/bots/commands`) work out of the box; stub anything else with `client.respond(method, path, response_or_exception)` - an unstubbed call raises immediately rather than hanging, so a forgotten stub fails the test loudly at the call it forgot, not a confusing assertion three lines later.

## Where to go deeper

`docs/framework.md` and the seven bot templates in [slim-bots](https://github.com/NC1107/slim-bots) cover the rest: command registration with the server, channel scoping and durable cursors, cooldowns and permission gates, the Canvas model for the Voice Canvas, typed event dispatch, and every safeguard's own reasoning.

## Installing an unreleased version

```
pip install "slim-m @ git+https://github.com/NC1107/slim-bots.git@main#subdirectory=slimbots"
```

Each template's `requirements.txt` pins one of the two install lines above.

## Licence

PolyForm Noncommercial 1.0.0 - see `LICENSE` in this package, or the [full text](https://polyformproject.org/licenses/noncommercial/1.0.0).
