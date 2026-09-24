# Changelog

All notable changes to `slim-m` (the `slimbots` package) are recorded here.
This project does not yet follow strict semantic versioning - it is pre-1.0, and a minor version can carry a breaking change, called out below.

## 0.3.1

A 0.2.0 database carried over into 0.3.0 could crash on a column that only 0.3.0's schema added - `bot-canvas-board`'s `items.added_by` was the one that actually broke in production (`OperationalError: no such column: added_by`).

- `slimbots.migrations.ensure_columns(conn, table, columns)`: a small library helper that adds a missing column to an existing table, idempotently, so a bot's `init_db` does not have to hand-roll it.
- `bot-canvas-board` now migrates `items.added_by` for a database carried over from 0.2.0.
- `bot-casino` now migrates its `hands` table's `hand_index`/`status` columns *and* rebuilds the table to widen its primary key, which a plain `ALTER TABLE` cannot do - a 0.2.0 database's in-progress hand is carried over as `hand_index = 0`.
- `bot-reminders`' existing hand-rolled `_ensure_column` is now `ensure_columns`, the shared helper, instead of a bot-local copy.
- Audited every other bot's schema against what 0.2.0 created; `bot-jellyfin` and `bot-modlog` had no drift to migrate.
- `bot.canvas(channel_id)`: a `Canvas` wired with the bot's own client and gateway, for a bot that draws on a channel other than the one a command came from.
- `bot-canvas-board` takes `!board` commands from an ordinary text channel and draws on a separately configured voice channel's canvas, via a new `CANVAS_CHANNEL` setting - backward compatible with the old single-channel setup, which now logs a startup warning instead of silently staying implicit.

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
