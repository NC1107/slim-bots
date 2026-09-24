# The slimbots framework

`slimbots` 0.3 is a discord.py-shaped framework, not just transport plumbing.
This is where the how-and-why lives, so docstrings in the code can stay one or two lines.
Read `bot-casino/` for a real bot built on it.

## Why the reversal

The pre-0.3 library was deliberately thin: no typed model, no cache, no event hierarchy.
Seven real bots later, every one of them had rebuilt its own regex command wall, its own member-lookup-by-name, and its own startup wiring.
That is the sign the abstraction was missing, not that each bot did something wrong, so 0.3 builds it once, in the library.

The pre-0.3 primitives (`Client`, `Connection`, `cursor`, `run_forever`) stay exported and working, unchanged, for the templates not yet ported to `Bot`.

## Shape

```python
from slimbots import Bot, Member

bot = Bot(prefix="!")

@bot.command(aliases=["bal"], help="Show your chip balance")
async def balance(ctx):
    await ctx.reply(f"{ctx.author.display_name}: {wallet(ctx.author.storage_key)} chips")

@bot.command(help="Give chips to someone", cooldown=5)
async def give(ctx, amount: int, member: Member):
    ...

@bot.event
async def on_member_removed(frame):
    ...

bot.run()  # reads SLIMM_URL and SLIMM_BOT_TOKEN from the environment
```

`Bot(...)` owns config, auth, the websocket, reconnect with backoff, the `seq`-less full-roster refresh, command registration, graceful SIGTERM, and `run()`/`start()`.
A bot script imports `Bot` and little else.

## Commands

`@bot.command(name=None, aliases=(), help=None, usage=None, cooldown=None, requires=None, check=None)` registers a function `async def name(ctx, *typed_args)`.
Arguments convert from the function's own annotations (`slimbots/commands.py`):

- `int` / `float` - parsed from one whitespace-separated token, or a clear `BadArgument` reply, never a traceback.
- `str` - one token, unless it is the *last* parameter, in which case it consumes the rest of the message (`!say hello there` -> `text="hello there"`).
- `Member` - resolved from `@username` or a bare username/id against `bot.space`, or a `BadArgument` naming the token that did not resolve.
- A parameter with a default is optional; a missing required one raises `MissingRequiredArgument` naming it.

`help` is auto-generated from the registered set (name, aliases, usage, help text) unless a bot registers its own `help` command first.

`@bot.check` registers an async predicate run before every command dispatch (a global cooldown/rate-limit rather than one command's own, for example): return a string to refuse with that reply, or `None`/falsy to let the command through.

## Identity

Store your own data under `member.storage_key` (an alias for the stable slim-m user id, never `username`, which can change).
Look a stored key back up to a live member with `await bot.space.member_for_key(key)` or `await bot.space.resolve_member(key)`, which fetches directly if the id fell out of the cached roster.
One line each way, by construction: neither name is spelled `id`, so a bot cannot casually key on the wrong field.

## The Space model

`bot.space` is `members`, `channels`, `roles` as live dicts, refreshed once per connect and reloadable on demand:

- `space.get_member(id_or_name)`, `space.get_channel(...)`, `space.get_role(...)`
- `await space.refresh_members()` / `refresh_channels()` / `refresh_roles()`
- `await space.grant_role(member, role)` / `revoke_role(...)` - real calls, not stubs, so a ported bot-roles hands out roles through the library.
- `Member.has_permission(permission)` checks the *caller's* base (deployment-level) permissions, the same set `GET /me` calls "base permissions", never the bot's own.
  `space.refresh_roles()` needs the bot's own token to hold `MANAGE_ROLES` (the same gate `GET /roles` has).
  Without it, every `has_permission` check answers `False`, deny by default, same as an unresolvable role-gated command anywhere else.

There is deliberately no `on_member_join` in this framework.
slim-m's wire protocol has no such event: `crates/slimm-server/src/http/ws/frames.rs` carries `member.removed`, `member.restored`, `member.timeout`, `member.role_changed` and `role.changed`, and nothing for a join.
Faking one from roster diffs would be a lie about what the wire actually says, so `on_member_removed`, `on_member_restored`, `on_member_timeout`, `on_member_role_changed` and `on_role_changed` are the real, honest set.

## Channel scoping and durable cursors

`Bot(channels={...})` restricts `message.created` dispatch to that set of channel ids; omitted, a bot answers wherever its role can see, same as before.
`on_raw_message(message)` fires for every in-scope message, command or not, before `on_message`/command dispatch - the hook a bot uses to persist a `seq` cursor, since `on_message` only fires for a non-command message.
`slimbots.catchup.bootstrap`/`sync` are async equivalents of the existing sync `cursor.bootstrap`/`sync`, for replaying a reconnect gap through `bot.process_message` before the gateway opens (`bot-casino/bot.py` is the worked example).
`cursor.get`/`cursor.set`/`cursor.init_table` are plain sqlite and need no async equivalent.

## Async HTTP

`slimbots.http.AsyncClient` is built on `httpx.AsyncClient` rather than `asyncio.to_thread` over `urllib`.
A bot's steady state is awaiting the websocket, and httpx gives real connection pooling and a timeout that composes with the rest of the event loop, instead of parking a thread-pool worker per in-flight request for something that is I/O-bound anyway.
httpx was already a transitive dependency in this environment, so it costs nothing new to vet.
`call()` retries a 429 or 5xx with exponential backoff (slim-m sends no `Retry-After`) and never retries a 401/403/other 4xx - a certain outcome, not an uncertain one.

The pre-0.3 sync `Client` (`urllib`) is untouched, for templates not yet ported.

## Safeguards, and how to opt out

All from PR #6's primitives, now built in rather than something a bot must remember to call:

| Safeguard | Default | Opt out |
| --- | --- | --- |
| Ignore other bots/webhooks | on | `Bot(ignore_bots=False)` |
| Never answer yourself | always on | not optional - see building-bots.md |
| Per-command cooldown | off | only set if `cooldown=` is passed |
| Permission gate | off | only set if `requires=` is passed |
| Input bounds/quotas | opt-in helpers | `slimbots.limits` - `require_len`, `require_range`, `require_int`, `Quota` |
| 403 from the bot's own missing permission | caught, replied clearly | a command can catch `ApiError` itself for a bespoke message |
| 401 (revoked token) | terminal, stops the process | not optional |
| 429 | exponential backoff, no crash | not optional |
| SIGTERM | clean shutdown, exit 0 | not optional - docker sends it on every redeploy |

Bot/webhook authorship (`slimbots/authors.py`) is resolved from the cached roster first and `GET /users/{id}` only as a fallback.
The one case that needs it is a webhook, which never appears in `GET /members` at all.

## Command registration

`@bot.command` registers itself with the server automatically, on every connect: `PUT /bots/commands` with `{prefix, commands: [{name, description, usage?, permission?}]}` (`slimbots/registration.py`), a full bulk overwrite each time, per decision 0031 in slim-m.
Every alias is registered as its own composer entry alongside its command's name, each truncated to the server's caps (name 1-32 `[A-Za-z0-9_-]`, description 1-100 chars, usage at most 80, at most 50 entries total).
A `requires=` permission becomes the entry's single-bit `permission`, which only hides the row in the composer for a caller who lacks it - it is never enforced against the message a bot receives, so the command's own `requires=` check still runs.
A 404 or 405 is treated as "server too old" and skipped quietly; any other error (400 naming the violated cap, 403 if the token somehow isn't a bot's) is raised, so the framework works against today's production server exactly as it will against tomorrow's.

## The embed seam

`ctx.send(content, embed=Embed(...))` and `ctx.reply(...)` already accept `embed=`.
Until slim-m has a real embed API, `Embed.render_fallback()` folds it into plain markdown text (`slimbots/embeds.py`, `slimbots/context.py::Context._render`).
When a real embed field lands on the wire, only `_render` changes - no bot's call site does.

## Testing

`slimbots.testing.FakeAsyncClient` is the async counterpart of the existing `FakeClient`: no network, pre-stubbed `/me`/`/channels`/`/members`/`/roles`, `respond(method, path, response_or_exception)` to stub anything else, and an unstubbed call fails loud rather than hanging.
Drive a `Bot` directly with `await bot.process_message({...})` to exercise argument conversion, cooldowns, permission gating, and the bot-ignore default with no deployment.
It is not a mock of slim-m's own validation or concurrency - `bot-casino/test_concurrency.py` is what proves money-safety, with a real sqlite connection and real threads.

## What each remaining bot needs to port

Not done this round (`bot-reminders`, `bot-jellyfin`, `bot-canvas-board`):

- **bot-reminders** - a persisted sqlite cursor across restarts, which the framework does not yet own (`bot.space` refreshes are in-memory only); recurring/timezone logic stays bot-specific either way.
- **bot-jellyfin** - polls an outside service, not slim-m's own events, so it mostly just needs `Bot`'s `ctx.send`/`space` for the outbound half; the polling loop stays its own.
- **bot-canvas-board** - canvas object routes have no `Command`/`Space` equivalent yet (no `Canvas` model); would need a small canvas-specific extension before the command layer buys it much.
