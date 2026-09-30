# Changelog

All notable changes to `slim-m` (the `slimbots` package) are recorded here.
This project does not yet follow strict semantic versioning - it is pre-1.0, and a minor version can carry a breaking change, called out below.

## Unreleased

- `Bot.moderation_head` is the `moderation_seq` the server's `hello` carried on the current connection, or `None` when the server sent none. It is set before `on_ready`.
- `Gateway.hello` keeps the whole hello frame.
- Bots that use it need `slim-m>=0.9.2`; `scripts/check_bot_pr.py` enforces it.
- `bot-modlog` persists the largest moderation `seq` it has seen, compares it with the head on each reconnect, and logs a gap marker when the head is still ahead after a short wait, attributed to a server restart when `/version` changed since its last connect.

## 0.9.1

- `Member.joined_at` is the server's account creation time in Unix milliseconds, or `None` when the payload has none. Since one deployment is one community, it is the join time.
- Bots that use it need `slim-m>=0.9.1`; `scripts/check_bot_pr.py` enforces it.

## 0.9.0

- `AsyncClient.get_message(channel_id, message_id)` and `Message.fetch(client, channel_id, message_id)` read one message by id, even one the bot never saw live.
  A channel the bot cannot view answers the same 404 as a missing message.
- `Message.attachments` is a list of `Attachment`, so a bot can pass their ids to `send(attachment_ids=...)`.
- `Channel.restricted` is `True` when `@everyone` cannot view the channel, and `None` when the server did not send it.
- Bots that use these need `slim-m>=0.9.0`; `scripts/check_bot_pr.py` enforces it.
- `bot-starboard` fetches a message it never saw, mirrors attachments, never mirrors a bot's or webhook's message, and will not mirror a possibly restricted channel into a public highlights channel.

## 0.8.0

- `space.find_member(user_id)` returns the cached member, else fetches it once, else `None`. `Bot._resolve_author` uses it.
- `bot.setting(name, type=bool)` and `type=dict` (`"a:1,b:2"`, order kept). A malformed dict entry is reported at `start()` with the missing-setting errors.
- `VoiceSession.leave` logs a warning when forgetting the heartbeat fails, instead of swallowing it.
- Bots that use these need `slim-m>=0.8.0`; `scripts/check_bot_pr.py` enforces it.
- Fix: `bot-jellyfin` announced the same episode or movie again when Sonarr or a manual replace swapped the file, since the new Jellyfin item has a new id and `DateCreated`. Announcements are now deduped in the bot's sqlite by series/season/episode, and by tmdb/imdb id or name and year for movies, within `JELLYFIN_DEDUPE_DAYS` (default 7, `0` is off). `JELLYFIN_REANNOUNCE_REPLACED=true` restores the old behaviour. The new `posted_media` table is added in place to an existing database.

## 0.7.1

- Fix: a bot with `listen_voice_chats` never heard a voice channel created, or made visible to it, after it connected. `space.channels` was only loaded on connect.
- `space.channels` now follows `channel.created`, `channel.updated` and `channel.deleted`, and is reloaded on `overwrite.changed`, `role.changed` and the bot's own `member.role_changed`, since those can make a channel newly visible.
- A frame for a channel id the space does not know triggers one channel reload, at most once per id per 30 seconds, as a backstop.
- A frame dropped because the bot does not listen in its channel is logged at debug level on the `slimbots.bot` logger.
- `Space.apply_channel_frame(frame)` is new.

## 0.7.0

Menu entries and call controls expect a slim-m server with bot-contributed UI (decision 0045, which stacks on decision 0039).

- New `@bot.message_menu(id, label, permission=None)`: adds a row to every message's context menu, under the bot's name and badge. The handler gets the same `Interaction` as a button, with `kind == "message_menu"` and the `message_id` it was used on.
- New `@bot.call_control(id, label, icon=None, permission=None)`: adds a button to the call dock while the bot is on the call. `icon` is one of `play`, `pause`, `stop`, `skip_next`, `skip_previous`, `volume`, `volume_off`, `repeat`, `shuffle`, `list`. The `Interaction` has `kind == "call_control"` and a `message_id` of `None`.
- The caps are the server's: 5 menu entries, 8 call controls, a 32-character label and a 64-character id. They are checked when the decorator runs.
- Both are registered with `PUT /bots/ui` on connect, only when the bot declares any, and quietly skipped on a server that predates it.
- `Interaction.kind` is new (`button` on a server that does not send it). `reply_ephemeral` and `ack` answer a use as they do a press; `edit_components` raises on a call control.
- `FakeAsyncClient` answers `PUT /bots/ui`, and `client.calls` records it.

## 0.6.0

Ephemeral replies expect a slim-m server with ephemeral messages (decision 0036).

- New `AsyncClient.time_out_member(user_id, duration_seconds, reason=None)` and `lift_member_timeout(user_id)`: put a member in timeout, or take them out of it. The bot needs the moderation permission; a missing one surfaces as the server's error.
- `FakeClient` records both calls, so a bot's tests can assert on them.
- `bot-automod` uses them.
- New `ctx.reply_ephemeral(text)` and `AsyncClient.send_ephemeral(channel_id, in_reply_to_id, text)`: answer only the author of a message, marked "Only you can see this". Never stored, so it is gone on reload and never reaches an offline member.
- `reply_ephemeral(..., public_fallback=True)` replies in the channel instead on a server that predates it (404/405). A 403 always raises.
- `FakeAsyncClient` records private answers in `client.ephemerals`, apart from `client.sent`.
- `bot-greeter` refuses `!welcome here` privately.
- New buttons on messages: `Button`, `rows()` and `send(components=...)`, with `@bot.button(custom_id)` / `@bot.button(prefix=...)` handlers that receive an `Interaction` naming who pressed. Expects the server's buttons support (decision 0039, which stacks on decision 0037).
- `Interaction.reply_ephemeral`, `.edit_components` and `.ack` answer a press; a handler that returns quietly is acked for it. `AsyncClient` gains `edit_components`, `ack_interaction` and `send_ephemeral_to_press`.
- `FakeAsyncClient` records `component_edits` and `acks`.

## 0.5.1

- New `VoiceSession.unpublish_screen_share()`: takes down the pair `publish_screen_share` published, so a bot can republish at another resolution or ceiling (a `VideoSource`'s size is fixed at construction). Safe to call twice.
- `FakeVoiceSession` gains `publish_count`/`unpublish_count` and the matching `unpublish_screen_share()`.
- `bot-jellyfin` uses it for `!quality <low|medium|high>`.

## 0.5.0

Two ways to scope where and how a bot answers, from one owner report: `!watch` typed in a voice channel's chat went unanswered, and every bot answering `!help` in a shared channel is a mess.

- Cause of the unanswered voice chat: a voice channel's chat is an ordinary channel of its own, and the server delivers its `message.created` to any bot that can view it. `Bot._handle_frame` then dropped the frame because the channel id was not in `channels` (`SLIMM_CHANNELS`, which names the text channel). Nothing on the server was wrong.
- New `Bot(listen_voice_chats=True)`, or `SLIMM_LISTEN_VOICE_CHATS=1`: a scoped bot also accepts messages (and channel events) from any voice channel's chat. Off by default, so existing bots are unchanged. `bot-jellyfin` turns it on.
- New `SLIMM_PREFIX`: overrides the `prefix=` a script passes, so an operator can give each bot in a shared channel its own without a code change. The default is still whatever the script passes (`!` in every template).
- New `Bot(mention_commands=True)` (the default): `@username <command>` runs a command exactly like the prefix does, case-insensitively, and only for this bot's own name. Pass `False` to keep the old prefix-only behaviour.
- `help` now opens with the bot's name and both ways to call it, for example `**jellyfin commands** (prefix `!`, or `@jellyfin <command>`)`, so several bots answering one channel can be told apart. `Bot.username` is set from `/me` on connect; before that the old header is used.
- A voice channel created after the bot connects is only recognised after its next reconnect, because `Space.channels` is refreshed on connect.

## 0.4.4

A prod bug: `!watch` failed with `module 'livekit.rtc' has no attribute 'AudioEncoding'`. 0.4.3's audio-bitrate ceiling called `rtc.AudioEncoding(...)`, but real livekit (1.1.20) re-exports `VideoEncoding` on `rtc` and not `AudioEncoding` - it lives only in the proto. The test fake had defined an `AudioEncoding` the real library does not, so the suite was green while every watch party threw.

- `publish_screen_share` now resolves `AudioEncoding` via the public name when present and falls back to `livekit.rtc._proto.room_pb2.AudioEncoding` otherwise; video was already correct.
- The `fake_rtc_module` no longer provides `AudioEncoding`, matching the real surface, and a new test drives the fallback branch so the old `rtc.AudioEncoding` shape cannot come back green.

## 0.4.3

`VoiceSession.publish_screen_share` now takes optional encoding ceilings, so a bot that publishes video is no longer stuck with the library default's ~1200 kbps floor and no ceiling.

- New `video_max_bitrate`, `video_max_framerate` and `audio_max_bitrate` kwargs, each `None` by default so existing callers are unchanged; when set they reach the LiveKit track as `VideoEncoding` / `AudioEncoding`.
- `bot-jellyfin`'s watch party uses them to match the WebRTC ceiling to Jellyfin's own transcode rather than letting the decent transcode be re-encoded back down, and to give continuous movie audio a 128 kbps stereo default instead of the unset speech-call tuning.
- `FakeVoiceSession` records the new kwargs, so a bot's tests can assert what it asked for.

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
