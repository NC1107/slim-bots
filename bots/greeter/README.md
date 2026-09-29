# greeter

A slim-m bot: posts a configurable welcome message when somebody joins the
Space - the worked example for `on_member_join`.

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
`on_member_join(member)` is one `@bot.event` handler instead of a manual
frame-type dispatch, and `Bot` itself owns `SLIMM_URL`/`SLIMM_BOT_TOKEN`/
`SLIMM_CHANNELS` - this script never imports `os`, reading `GREETER_MESSAGE`
through `bot.setting()` instead. `bot-ping` stays free
of the library on purpose; see its own README.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
python3 bot.py
```

It needs `VIEW_CHANNEL` and `SEND_MESSAGES` in the welcome channel - see
"What your bot may do" in `docs/bots/building-bots.md`.

## Where welcomes go

First match wins:

1. `SLIMM_CHANNELS`, if it names exactly one channel id: a hard pin, for a
   deployment that wants it fixed in config.
2. `!welcome here`, run in a channel by someone with Manage Server. The
   choice is stored in the bot's sqlite file (`SLIMM_DB_PATH`, default
   `greeter.db`), so it survives a restart. `!welcome` alone says where
   welcomes currently go.
3. `GREETER_CHANNEL` (default `general/chat`): a `category/channel` path, or
   a bare channel name, matched case-insensitively.

If none of those resolves, a join posts nothing and logs why, rather than
guessing a channel.

## Settings

- `GREETER_MESSAGE` (default `Welcome, {member}! Make yourself at home.`) -
  the message text. `{member}` becomes an `@mention` of whoever joined; any
  other `{...}` is left alone and would raise on a genuine typo, logged by
  the framework's own `guard_dispatch` rather than crashing the bot.
- `GREETER_CHANNEL` (default `general/chat`) - see above.

## Why there is no embed

The welcome is plain `content`, because the `@mention` in it is what notifies the new member.
An embed repeating the same text showed the welcome twice, and its body does not render the mention as a pill.

## What this deliberately does not do

- **Greet a restored member.** `member.joined` does not fire for
  `DELETE /members/{id}/removal` - that member was never new, and
  `on_member_restored` already exists for that case. See
  `crates/slimm-server/src/hub/event.rs`'s own doc comment on
  `MemberJoined` for why.
- **Rate-limit or batch joins.** Each `member.joined` is one post; a bulk
  invite redemption posts one welcome per member, in whatever order the
  events arrive.
- **Remember who it already greeted.** There is nothing to deduplicate
  against: the event fires exactly once per real join (registration, or an
  invite redemption), never again for the same member, so this bot keeps
  no database of its own.
- **Direct-message the new member.** v1 DMs are same-deployment only and
  this bot has no reason to open one; the welcome goes to the configured
  channel, the same as every other bot's output here.
