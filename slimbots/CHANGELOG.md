# Changelog

All notable changes to `slim-m` (the `slimbots` package) are recorded here.
This project does not yet follow strict semantic versioning - it is pre-1.0, and a minor version can carry a breaking change, called out below.

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
- Built-in safeguards: bot/webhook ignore, per-command cooldowns, permission gates against the caller's real permissions, 429 backoff, 401 as terminal, clean SIGTERM shutdown

## 0.2.0 and earlier

Pre-framework: `Client`, `Connection`, `run_forever`, `cursor`, `testing.FakeClient`. Each bot template implemented its own command parsing, member lookup, and reconnect handling on top of these.
