# greeter

A slim-m bot: posts a configurable welcome message when somebody joins the
Space - the worked example for `on_member_join`.

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
`on_member_join(member)` is one `@bot.event` handler instead of a manual
frame-type dispatch, and `Bot` itself owns `SLIMM_URL`/`SLIMM_BOT_TOKEN`/
`SLIMM_CHANNELS` - this script never imports `os`, reading `GREETER_MESSAGE`
and `GREETER_TITLE` through `bot.setting()` instead. `bot-ping` stays free
of the library on purpose; see its own README.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
python3 bot.py
```

`SLIMM_CHANNELS` should name exactly one channel here - `bot.channel` is
the one this bot posts into. It needs only `VIEW_CHANNEL` and
`SEND_MESSAGES` there - see "What your bot may do" in
`docs/bots/building-bots.md` for how to grant a bot a channel overwrite.

## Settings

- `GREETER_MESSAGE` (default `Welcome, {member}! Make yourself at home.`) -
  the message text. `{member}` becomes an `@mention` of whoever joined; any
  other `{...}` is left alone and would raise on a genuine typo, logged by
  the framework's own `guard_dispatch` rather than crashing the bot.
- `GREETER_TITLE` (default `New member`) - the embed's title.

## Why an embed rides alongside the plain text

`Embed(title=, description=)` carries the same message a plain-text client
already sees in `content`, so nothing is lost for an older client - see
`../../docs/framework.md`'s embeds section for the fallback path. A future
version could add the new member's avatar or join count once slim-m's
embed schema (decision 0030) grows an image field a bot can point at one.

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
