# The slimbots framework

`slimbots` 0.3 is a discord.py-shaped framework, not just transport plumbing.
This is where the how-and-why lives, so docstrings in the code can stay one or two lines.
Read `bot-casino/` for a real bot built on it.

## Why the reversal

The pre-0.3 library was deliberately thin: no typed model, no cache, no event hierarchy.
Seven real bots later, every one of them had rebuilt its own regex command wall, its own member-lookup-by-name, and its own startup wiring.
That is the sign the abstraction was missing, not that each bot did something wrong, so 0.3 builds it once, in the library.

Every template in this repo is on `Bot` now; the pre-0.3 sync primitives (`Client`, `Connection`, `run_forever`, `cursor.bootstrap`/`sync`, `testing.FakeClient`) are gone entirely rather than kept around unused - see "Removed in 0.3.0" below.
`bot-ping` stays free of the library on purpose and never used them either; nothing else in the repo needed them once the port finished.

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

`@bot.command(name=None, aliases=(), help=None, usage=None, cooldown=None, cooldown_bucket="user", requires=None, check=None)` registers a function `async def name(ctx, *typed_args)`.
Arguments convert from the function's own annotations (`slimbots/commands.py`):

- `int` / `float` - parsed from one whitespace-separated token, or a clear `BadArgument` reply, never a traceback.
- `str` - one token, unless it is the *last* parameter, in which case it consumes the rest of the message (`!say hello there` -> `text="hello there"`).
- `Member` - resolved from `@username` or a bare username/id against `bot.space`, or a `BadArgument` naming the token that did not resolve.
- `Duration` - `10m`, `2h30m`, `1d` parsed to an int count of seconds (`slimbots/converters.py`); replaces a bot's own duration regex.
- `TimeOfDay` - `HH:MM` parsed to a `.hour`/`.minute` pair, range-checked; replaces a bot's own clock regex.
- A parameter with a default is optional; a missing required one raises `MissingRequiredArgument` naming it.

`help` is auto-generated from the registered set (name, aliases, usage, help text) unless a bot registers its own `help` command first.

`cooldown_bucket` picks who a `cooldown=` applies to: `"user"` (the default - each member gets their own timer), `"channel"` (one timer shared by everyone in that channel), or `"deployment"` (one timer for the whole bot, every channel). A `@group.command()` subcommand takes the same two keywords independently of its group's.

`@bot.check` registers an async predicate run before every command dispatch (a global cooldown/rate-limit rather than one command's own, for example): return a string to refuse with that reply, or `None`/falsy to let the command through.

`@bot.group(name=None, ...)` registers a command that dispatches its first argument token to a `@group.command(name=...)`-registered subcommand (`!remind in 10m text` -> the `remind` group's `in` subcommand), falling back to the group's own decorated function for a bare or unrecognised invocation.
This is what replaced every bot's own `TRIGGER_X = re.compile(...)` wall of shapes under one command name; `bot-reminders`' `!remind`/`!reminders` are the worked example.
`build_help_text` descends into a subcommand (`!help remind in`) the same way; command registration still counts a whole group as one entry, not one per subcommand (below).

A prefixed message naming no registered command is silent by default - it fires `on_command_not_found(ctx, error)` if, and only if, something is actually listening for it, so a stray `!` in ordinary conversation costs nothing (no author lookup, no `ctx` built) when nobody cares.
`error` is a real `CommandNotFound` (`error.invoked_with`, `str(error)`), not just a name string, for the same reason every other command failure is a real exception rather than a tuple.

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

## Typed events

Every frame kind `frames.rs` defines gets an `@bot.event` handler now, not just the eight the framework already dispatched - `on_message_edited`, `on_message_deleted`, `on_reactions_changed`, `on_thread_updated`, `on_message_pinned`/`on_message_unpinned`, `on_poll_voted`, `on_presence_changed`, `on_profile_changed`, `on_typing_started`/`on_typing_stopped`, `on_channel_created`/`on_channel_updated`/`on_channel_deleted`, `on_category_changed`, `on_overwrite_changed`, `on_voice_activity`, `on_call_ringing`/`on_call_ring_ended`, the remaining canvas events (`on_canvas_objects_restored`, `on_canvas_cursor_moved`, `on_canvas_stroke_preview_updated`, `on_canvas_object_moved`, `on_canvas_object_reordered`, `on_canvas_media_slot_changed`), and `on_reports_changed`.
Each of these hands the handler a small typed object from `slimbots/events.py` (`event.channel_id`, `event.message`, and so on) instead of the raw frame dict - the eight pre-existing handlers keep getting the raw frame, unchanged, so a bot that reads `frame["user_id"]` today does not break.
`bot._handle_frame`'s own dispatch tables (`_GLOBAL_EVENT_FRAMES`/`_CHANNEL_EVENT_FRAMES`) now map a frame kind to `(handler_name, payload_class)`; `payload_class` of `None` means "pass the raw frame", which is how the eight originals stay backward compatible.

Global versus channel-scoped follows the same test as before: an event about a channel's own activity (a message edit, a reaction, typing, canvas ops) is channel-scoped, gated the same way `message.created` is; an event about something that is not naturally inside one of a bot's watched channels - a member's presence, a channel being created in the first place, a DM call ring - is global, ungated by `channels`.
`Message.fetch()` (a single-message GET) and `on_voice_*` join/leave/screen-share events are deliberately not here yet: slim-m has neither route nor frame for them at the time of writing. Add them once those land server-side rather than guessing the shape now.

## The Canvas model

`Canvas(bot.client, channel_id)` wraps one channel's Voice Canvas: `await canvas.place(kind, x=, y=, w=, h=, props=)`, `move(object_id, x=, y=)`, `remove(object_ids)`, `clear(before_seq)`, `restore(target_op)`, `reorder(object_id, z_index)`, and `viewport(min_x=, min_y=, max_x=, max_y=)` for a bounded-rectangle read - `bot-canvas-board` is the worked example (a fixed-size sticky-note board).
`clear`/`restore`/`reorder` are the same `POST canvas/ops` endpoint `move`/`remove` already use, just a different `kind` - `clear` needs MANAGE_CANVAS unconditionally and a `before_seq` fence (never optional, so a lost response retried without one cannot wipe an interval it should not); `restore` names a prior `remove`/`clear` op's own id, not an object id; `reorder` takes an explicit `z_index` the caller computes (typically one above/below every value it already knows, for "bring to front"/"send to back") rather than a server-computed delta.
`on_canvas_object_placed`, `on_canvas_objects_removed` and `on_canvas_cleared` are channel-scoped events - dispatched only for a channel in `channels`, the same gate `message.created` gets, since (unlike the member/role events) these carry a `channel_id` and are not deployment-wide.

## Config, settings, channel scoping, and durable cursors - all owned by Bot

`Bot()` reads `SLIMM_URL`/`SLIMM_BOT_TOKEN` itself, and `SLIMM_CHANNELS` (comma-separated ids) too when `channels=` is not passed explicitly - a bot script never needs `import os` just to read these three.
`Bot(require_channels=True)` folds a missing `SLIMM_CHANNELS` into the same one-line `RuntimeError` as a missing URL/token, instead of the bot re-checking it.
`bot.channel` is the lone configured channel when `channels` names exactly one - the common case for a bot that posts to one place.

`bot.setting(name, default=None, *, type=str, required=False)` reads one of a bot's *own* env vars the same way, converting via `type` (`int`, `float`, or `list` for a comma-separated one) - `bot-jellyfin`'s nine `JELLYFIN_*` variables are the worked example.
A missing `required=True` value is never raised at the `setting()` call itself (which usually runs at import time, before `Bot.start()`); it is collected and reported together with a missing `SLIMM_URL`/token/channels in the same one-line error when `start()` runs.

## Background tasks

`bot.background(coro, *, name=None)` is the one way a bot should ever start a loop that outlives one command - `bot-casino`'s hourly prune, `bot-reminders`' due-checker and pruner, `bot-jellyfin`'s poll loop.
A plain `asyncio.create_task(...)` is only weakly referenced by the event loop, so a fire-and-forget task like that can be garbage-collected mid-run and stop silently; `bot.background` stores the task in `bot._background_tasks` for as long as it is running, which is also what a shutdown walks to cancel every one of them cleanly.
An exception out of a background task (a 401 included, which stays terminal exactly like one on the socket) is treated as fatal: it is logged, the bot's main loop is cancelled, and `bot.run()` exits non-zero so a container orchestrator restarts it, rather than the task's failure going unnoticed while the rest of the bot carries on.
Call it from `on_connect`/`on_ready`, the same place a bot used to reach for `asyncio.create_task` directly.

`Bot(default_data_path="casino.db")` gives `bot.data_path`: `SLIMM_DB_PATH` if set, else that default - the one place a bot's own sqlite file path is derived, instead of every bot re-deriving `os.environ.get("SLIMM_DB_PATH", "...")` by hand.

A bot with `channels` set gets a persisted, cross-restart `seq` cursor for free, at `bot.data_path` when the bot has one (sharing the same file as its business data, the way `bot-casino` does) or a generic default (`slimbots-cursor.db`) otherwise; `Bot` bootstraps and `/sync`-replays the backlog through `process_message` on every connect, before `on_ready` fires - no bot code calls `cursor`/`catchup` directly any more.
`cursor_path=` on `Bot()` still exists to point the cursor at a file *other* than `data_path`, for the rare bot that wants them separate.

`on_raw_message(message)` still fires for every in-scope message, command or not - for a bot that wants its own hook into every message, not for cursor-keeping any more.
`on_frame(frame)` fires for every frame of any type, recognised or not, before any other dispatch - a liveness signal (a modlog-style bot marking itself "still connected") is the reason this exists; most bots have no reason to listen for it.
`slimbots.catchup`/`cursor` are still there for a bot that wants to manage its own separate cursor outside what `channels=` already covers, but neither is needed for the common case any more.

## The async store

`Bot(store_migrate=init_db)` opens `bot.store` automatically during `start()`, off the event loop, the same way `channels=` opens a cursor - most bots never need to call `open_store` by hand.
`await bot.open_store(migrate=init_db)` does the same thing directly: opens a `Store` at `bot.data_path` (or an explicit `path=`) and returns it as `bot.store`; calling it again just returns the same one.
`Store` owns a single sqlite connection but runs every query in a worker thread via `asyncio.to_thread`, one call at a time, so `await bot.store.run(get_balance, ctx.author.id)` never blocks the event loop the way a bare `bot.db.execute(...)` used to.
`migrate` is a plain `def migrate(conn): ...` run once, off the event loop, right after the connection opens - `init_db` in every bot that already had one.

The functions passed to `run()` are unchanged from before this - `get_balance(conn, user_id)`, `due_reminders(conn, now)`, and so on all still take a raw `sqlite3.Connection` and run synchronously; `run()` just moves *where* that call happens.
That is why `bot-casino/test_concurrency.py` did not need to change at all: it drives those same functions directly against its own real connections on real threads, never through `Bot` or `Store`.
A background sweep that already ran on its own thread (`bot-jellyfin`'s poster refresh used to reach for `asyncio.to_thread` by hand) can just call `bot.store.connection` directly instead, since it was never blocking the event loop in the first place.

## Async HTTP

`slimbots.http.AsyncClient` is built on `httpx.AsyncClient` rather than `asyncio.to_thread` over `urllib`.
A bot's steady state is awaiting the websocket, and httpx gives real connection pooling and a timeout that composes with the rest of the event loop, instead of parking a thread-pool worker per in-flight request for something that is I/O-bound anyway.
httpx was already a transitive dependency in this environment, so it costs nothing new to vet.
`call()` retries a 429 or 5xx with exponential backoff (slim-m sends no `Retry-After`) and never retries a 401/403/other 4xx - a certain outcome, not an uncertain one.
`raw_body=` sends bytes as-is instead of JSON-encoding `body`, for an attachment upload (`bot-jellyfin`'s poster re-hosting is the worked example).

## Typed REST wrappers

`AsyncClient.send()` returns a `Message` now, not a raw dict: `await msg.edit(content)`, `await msg.delete()`, `await msg.react(emoji)`/`remove_reaction(emoji)`, `await msg.pin()`/`unpin()`, `await msg.vote(option)`, `await msg.open_thread()` (returns a `Channel`).
`bot-roles`' listing-message edit and `bot-jellyfin`'s poster upload used to be a raw `bot.client.call("PATCH", ...)`/`call("POST", "/attachments?...", raw_body=...)` - both are the named wrapper now (`edit_message`/`upload_attachment`).
`AsyncClient.upload_attachment(data, filename=)` returns an `Attachment` whose `.id` is exactly what `send`'s `attachment_ids` takes, so there is no raw dict in between.
`AsyncClient.list_dms()`/`open_dm(user_id)`/`close_dm(user_id)` wrap the DM routes, each returning (or listing) a `DmConversation`; a DM's `channel_id` works with every ordinary channel route, `send` included.

Every one of these is still a thin, hand-written wrapper over one REST call rather than a generated client - it is not route-shaped, it is "one class with the actions people actually reach for bound to it".

## Sending over the gateway

`typing`, `canvas.cursor` and `canvas.stroke_preview` are client->server frames, not REST calls - slim-m's own rate limits and authorization for them mirror the REST routes they stand in for, but there is no request/response, just a frame sent into the open socket.
`await bot.send_frame(frame)` sends one, raising a clear `RuntimeError` rather than hanging if nothing is connected yet; `await bot.start_typing(channel_id)` is the one-line form.
`async with ctx.typing():` is the one most commands want: it sends the initial frame on entry and refreshes every four seconds (slim-m's typing TTL is six) for as long as the block runs, so a slow command's typing indicator does not lapse partway through - `ctx.typing()`'s refresh loop is a plain, locally-scoped task cancelled on exit, not a `bot.background()` task, since nothing about it needs to outlive one command.
`Canvas(client, channel_id, bot=bot)`'s `send_cursor(x, y)` and `send_stroke_preview(object_id, points, ended=)` need that `bot=` (a clear `RuntimeError` otherwise) since they go over the gateway, not REST - the REST-only methods (`place`/`move`/`remove`/`clear`/`restore`/`reorder`/`viewport`) never needed it and still do not.

## Waiting for a reply

`await bot.wait_for(event, check=, timeout=)` waits for the next dispatch of an `on_*` event (a typed one from the previous section included) where `check(*args)` is true, or raises `asyncio.TimeoutError`; it registers and removes a temporary listener under the hood, so it composes with a bot's real `@bot.event` handlers rather than replacing them.
`await ctx.confirm(prompt, timeout=30)` is the one most commands want: it replies with `prompt` plus `(yes/no)`, waits for the same author's next message in the same channel via `wait_for("on_raw_message", ...)`, and returns `True`/`False` - a timeout answers `False`, the safe default for anything a confirmation is guarding.
`bot-canvas-board`'s `!board clear` is the worked example: it used to require retyping `!board clear yes` to confirm; it now asks and waits for a real reply.

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
A `@bot.group()` still contributes exactly one entry, whatever it names, not one per `@group.command()` subcommand - its `usage` is the subcommand names joined by `|` instead, so a bot with several grouped shapes cannot blow past the cap the way registering every subcommand separately would.
A `requires=` permission becomes the entry's single-bit `permission`, which only hides the row in the composer for a caller who lacks it - it is never enforced against the message a bot receives, so the command's own `requires=` check still runs.
A 404 or 405 is treated as "server too old" and skipped quietly; any other error (400 naming the violated cap, 403 if the token somehow isn't a bot's) is raised, so the framework works against today's production server exactly as it will against tomorrow's.

## Embeds

`ctx.send(content, embed=Embed(...))` and `ctx.reply(...)` send the real `RequestEmbed` wire shape decision 0030 defines (title/description/url/color/author/fields/footer/timestamp/image/thumbnail), capped to the same limits the server enforces (10 embeds/message elsewhere is a bot's own concern; per-embed caps live in `slimbots/embeds.py`).
`content` is never sent blank - slim-m refuses that - so a bare `embed=` with no `content` sends the embed's own `render_fallback()` text alongside the real embed.
If the server rejects the request with `embeds` present (an older deployment), `AsyncClient.send` retries once, under the same message id, as plain fallback text - the one case `render_fallback()` is the whole reply rather than a companion to it.

## Testing

`slimbots.testing.FakeAsyncClient` never touches the network: pre-stubbed `/me`/`/channels`/`/members`/`/roles`, `respond(method, path, response_or_exception)` to stub anything else, and an unstubbed call fails loud rather than hanging.
Drive a `Bot` directly with `await bot.process_message({...})` to exercise argument conversion, cooldowns, permission gating, and the bot-ignore default with no deployment; `await bot._handle_frame({...})` reaches an `@bot.event` handler the same way.
It is not a mock of slim-m's own validation or concurrency - `bot-casino/test_concurrency.py` is what proves money-safety, with a real sqlite connection and real threads.

## Removed in 0.3.0

Every template in this repo is on `Bot`.
`Client`, `Connection`, `run_forever`, `call_with_retry`, `cursor.bootstrap`/`sync`, and `testing.FakeClient` - the pre-0.3 sync primitives a bot built by hand on top of - are gone rather than kept unused: `bot.py` for a ported bot no longer has two different ways to do the same thing to choose between.
`cursor.get`/`set`/`init_table` (plain sqlite, no network) and `client.socket_url` stayed, since `Bot` itself still uses them internally.
`bot-ping` never used any of this - it is still the one file that shows the whole protocol by hand, on purpose.
