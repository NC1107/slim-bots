# Changelog

All notable changes to `slim-m` (the `slimbots` package) are recorded here.
This project does not yet follow strict semantic versioning - it is pre-1.0, and a minor version can carry a breaking change, called out below.

## 0.4.2

A prod bug: `!watch iron man` gave the numbered picker, a reply within seconds still timed out 60s later with "timed out - `!watch` again to retry".

- `Bot._handle_frame` used to await `process_message` inline, so the single sequential gateway loop (`async for frame in gateway.frames(): await self._handle_frame(frame)`) was blocked for as long as a command ran - including inside a command's own `bot.wait_for`/`ctx.confirm`. The reply frame that would resolve the wait could never be read until the wait's own timeout fired: a deadlock, not a race, for every command that waits on a later message.
- `_handle_frame` now runs `process_message` as its own `bot.background()` task instead, so the frame loop keeps reading while a command is still in progress. Task creation order still matches frame arrival order; completion order does not (a slower command can finish after a faster later one) - a per-channel queue was considered and rejected, since serializing to completion would reintroduce the same deadlock one level down for a reply in the same channel.
- Confirmed `ctx.confirm` and `bot-canvas-board`'s `!board clear` hit the identical deadlock and are fixed by the same change; both had test-level workarounds that existed only to dodge it.
- Confirmed the picker's `same_place` check (`message.get("channel_id")`/`message.get("author_id")`) against the real wire `MessageDto` - both are genuine top-level fields, no fix needed there.
- New regression test drives `_handle_frame` sequentially, the way the real gateway does, instead of calling `process_message` directly - the old tests could not have caught this since they never exercised the frame loop itself.

## 0.4.1

`!watch` required the invoker to be in the *command channel's* own voice call - a check that could never pass once `!watch` was typed in an ordinary text channel, since a text channel has no voice call of its own. The bot always replied "join this channel's voice call first," regardless of which real voice channel the person was actually in.

- `bot.voice.find_member(user_id)`: the voice channel a member is actually connected to, or None - live `voice.participant_joined`/`voice.participant_left` events (decision 0032) update an in-memory cache, a cache miss falls back to a concurrent roster scan of every voice channel the bot can see.
- `bot-jellyfin`'s `!watch` now joins the invoker's real voice channel, wherever the command was typed; refuses with "join a voice channel first, then run `!watch` again" only if they are in no call, and names the missing `CONNECT`/`SPEAK` permission if the bot can't join or can't speak there. The confirmation names the voice channel, e.g. "streaming Iron Man into #voice".
- Audited every other bot for the same "the command's channel is where the action happens" assumption; `bot-canvas-board` already has an explicit `CANVAS_CHANNEL` (0.4.0), and the rest (`bot-casino`, `bot-reminders`, `bot-modlog`, `bot-roles`) legitimately act on the channel a command came from - no other bot needed a change.

## 0.4.0

A 0.2.0 database carried over into 0.3.0 could crash on a column that only 0.3.0's schema added - `bot-canvas-board`'s `items.added_by` was the one that actually broke in production (`OperationalError: no such column: added_by`).

- `slimbots.migrations.ensure_columns(conn, table, columns)`: a small library helper that adds a missing column to an existing table, idempotently, so a bot's `init_db` does not have to hand-roll it.
- `bot-canvas-board` now migrates `items.added_by` for a database carried over from 0.2.0.
- `bot-casino` now migrates its `hands` table's `hand_index`/`status` columns *and* rebuilds the table to widen its primary key, which a plain `ALTER TABLE` cannot do - a 0.2.0 database's in-progress hand is carried over as `hand_index = 0`.
- `bot-reminders`' existing hand-rolled `_ensure_column` is now `ensure_columns`, the shared helper, instead of a bot-local copy.
- Audited every other bot's schema against what 0.2.0 created; `bot-jellyfin` and `bot-modlog` had no drift to migrate.
- `bot.canvas(channel_id)`: a `Canvas` wired with the bot's own client and gateway, for a bot that draws on a channel other than the one a command came from.
- `bot-canvas-board` takes `!board` commands from an ordinary text channel and draws on a separately configured voice channel's canvas, via a new `CANVAS_CHANNEL` setting - backward compatible with the old single-channel setup, which now logs a startup warning instead of silently staying implicit.
- `bot.voice.join(channel_id)`: joins a voice call over LiveKit, keeping a heartbeat running in the background so the server's 40s heartbeat timeout doesn't evict it; `VoiceSession.publish_screen_share()` publishes a video+audio pair tagged as a screen share, rendered by a client with no bot-specific code on that end. `livekit.rtc` loads through `importlib` rather than a normal import, so a bot that never touches voice - and CI - never needs the package installed.
- `bot-jellyfin` gets a watch party: `!watch <title>`, `!pause`, `!resume`, `!seek <h:mm:ss>`, `!np`, `!subs <lang|off>`, `!stop` - jellyfin transcodes server-side, a local ffmpeg decodes that into raw frames pushed into the source `bot.voice` hands back.
- `slimbots.testing.FakeVoice`/`FakeVoiceSession` stand in for the real LiveKit connection so a bot's voice-touching tests never need `livekit`/`ffmpeg` installed.

## 0.3.0

The framework rewrite: `Bot()`, `@bot.command`/`@bot.group()`, typed argument conversion, `ctx`, a live `Space`/`Canvas` model, `bot.setting()`, `bot.background()`, and real embeds.
Every bot template in [slim-bots](https://github.com/NC1107/slim-bots) is on it now.
See `docs/framework.md` for the full shape.

### Removed

The pre-0.3 sync primitives every template used to hand-roll around are gone, not deprecated - a bot on 0.2.0 needs to move onto `Bot` to upgrade, not just bump a pin.

| 0.2.0 | 0.3.0 |
| --- | --- |
| `Client` (sync, urllib-based REST + auth) | `Bot` owns auth and connection; use `bot.client` (an `AsyncClient`, httpx-based) for a raw call |
| `Connection` (sync websocket handshake) | `Bot` owns the connection; `@bot.event`/`@bot.command` replace reading frames by hand |
| `run_forever` (the reconnect loop) | `bot.run()` / `await bot.start()` |
| `call_with_retry` | `AsyncClient.call`'s own built-in retry (a network error, a 5xx, or a 429; never a rejected 4xx) |
| `cursor.bootstrap` / `cursor.sync` (sync) | `catchup.bootstrap` / `catchup.sync` (async), or nothing - `Bot(channels=...)` catches up automatically on connect |
| `testing.FakeClient` | `testing.FakeAsyncClient` |

`cursor.get`/`set`/`init_table` (plain sqlite, no network) and `client.socket_url` are unchanged - `Bot` still uses both internally.

### Added

- `Bot()`, `@bot.command`, `@bot.group()`/`@group.command()`, `ctx.reply`/`ctx.send`
- Typed argument conversion: `int`, `float`, `Member`, `Duration`, `TimeOfDay`
- `bot.space` (members/channels/roles) and `bot.space.grant_role`/`revoke_role`
- `Canvas` for a channel's Voice Canvas (`place`/`move`/`remove`/`viewport`)
- `bot.setting()` for a bot's own env vars, reported alongside a missing `SLIMM_URL`/token/channels
- `bot.data_path`, `bot.background()` for a supervised task that outlives one command
- Real embeds (`Embed`), with a markdown fallback for a server that does not know the field yet
- Command registration with the server (`PUT /bots/commands`), a no-op against a server too old to have the route
- Built-in safeguards: bot/webhook ignore, per-command cooldowns (per user, per channel, or per deployment via `cooldown_bucket=`), permission gates against the caller's real permissions, 429 backoff, 401 as terminal, clean SIGTERM shutdown
- Typed `on_*` dispatch for every websocket frame kind, not just the original eight (`slimbots.events`)
- Typed REST wrappers: `Message.edit`/`.delete`/`.react`, reactions, pins, threads, polls, attachment upload, DMs
- `Canvas.clear`/`.restore`/`.reorder`, alongside the existing `place`/`move`/`remove`/`viewport`
- A gateway send path: `ctx.typing()`, `canvas.send_cursor()`, `canvas.send_stroke_preview()`
- `bot.wait_for(event, check=, timeout=)` and `ctx.confirm(prompt)`
- `on_command_not_found(ctx, error)`, dispatched only when a bot actually listens for it
- `bot.load_extension(module)` to split a bot across files
- `py.typed` and real type hints on the public surface, checked by `pyright` in CI

## 0.2.0 and earlier

Pre-framework: `Client`, `Connection`, `run_forever`, `cursor`, `testing.FakeClient`. Each bot template implemented its own command parsing, member lookup, and reconnect handling on top of these.
