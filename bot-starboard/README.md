# bot-starboard

Mirrors a message into a chosen "highlights" channel once enough people react
to it with a chosen emoji - a starboard, the best-loved and lowest-risk kind
of Discord utility bot: it never acts on anyone, it just makes a good message
easier to find later.

Built on the `slimbots` `Bot` framework - see `../docs/framework.md`. No
commands: this bot only ever reacts to `on_reactions_changed`,
`on_message_edited`, and `on_message_deleted`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-1>,<channel-2>,... \
STARBOARD_CHANNEL=<highlights-channel-id-or-name> \
python3 bot.py
```

`SLIMM_CHANNELS` must list every channel a message can be starred from - only
a watched channel's `reactions.changed`/`message.edited`/`message.deleted`
frames ever reach this bot. `STARBOARD_CHANNEL` names where highlights are
posted; it does not need to be in `SLIMM_CHANNELS` unless you also want it
watched for some other reason. There is no single-channel fallback the way
`bot-canvas-board`'s `CANVAS_CHANNEL` has - nothing is a safe default for
where highlights go, so a missing or unresolvable `STARBOARD_CHANNEL` refuses
to start.

Two more settings, both optional:

- `STARBOARD_EMOJI` - the emoji that counts toward the threshold. Defaults to
  a star (the classic starboard emoji).
- `STARBOARD_THRESHOLD` - how many reactions of that emoji a message needs
  before it is mirrored. Defaults to 3.

The bot needs `VIEW_CHANNEL` on every channel in `SLIMM_CHANNELS` and
`VIEW_CHANNEL`/`SEND_MESSAGES` on `STARBOARD_CHANNEL`. See "Getting a token"
and "What your bot may do" in `docs/bots/building-bots.md`.

## What crossing the threshold does

The highlight is one message in `STARBOARD_CHANNEL`: its `content` is just
`⭐ **5**` (the emoji and the live count - this is the part that changes, so
it lives in the one field a message can actually be edited after it is
sent), and an embed alongside it credits the author, quotes the message, and
names the origin channel in its footer as the reference back to where it
came from. Any image attachments on the original ride along on the highlight
too, referenced by their existing content-addressed id rather than
downloaded and re-uploaded.

- **The count updates in place** as more (or fewer) reactions come in -
  `Message.edit()` on the same highlight message, never a new post.
- **An edit to the original re-posts the highlight** with the new content.
  This is a delete-and-resend, not an in-place edit: slim-m has no route to
  change a message's embeds after it is sent, only its `content`, so the
  quoted text - which lives in the embed - can only change by sending a new
  one. The stored count carries over unchanged; only the quoted content
  and any image attachments are refreshed.
- **A delete removes the highlight.** Deleting an already-removed highlight
  (a moderator beat this bot to it) is treated the same as if it had never
  existed, not an error.

## Never mirrored

- **A bot's or webhook's own message.** Checked against the *original
  message's author*, not the reactor - reactions carry no per-reactor
  identity on the wire anyway (`reactions.changed` is an aggregate count).
  This also means the highlight this bot itself posts can never be starred
  into another highlight: its own author is this bot.
- **A message this bot can no longer see or fetch.** `reactions.changed`
  only ever names a channel and a message id, so every highlight is built
  from a fresh `AsyncClient.get_message`/`Message.fetch`
  (`GET /channels/{channel}/messages/{message}`) rather than anything
  cached from a `message.created` this bot happened to see live - the same
  route now also means this bot can star a message it never saw live at
  all, not just ones sent since it started.
- **A message from a channel the starboard's audience cannot already see.**
  `Channel.restricted` (whether `@everyone` holds `VIEW_CHANNEL` there) is
  checked explicitly before ever fetching or posting anything: if the origin
  channel is restricted and `STARBOARD_CHANNEL` is not, mirroring it would
  make a private channel's content visible to a wider audience than the
  channel owner chose, so this bot refuses instead. See "What this
  deliberately does not do" for the one gap this check does not close.

## Safeguards

- **The leak check above**, checked once at the moment a message would first
  cross the threshold - not re-checked on a later edit or count change,
  since a channel's visibility rarely changes and the messages that would
  need it to are already caught at creation.
- **It never answers another bot** for anything - `Bot`'s own bot-ignore
  default, though this bot has no commands for that default to matter to in
  the first place.

## What this deliberately does not do

- **A full permission-overwrite comparison.** `Channel.restricted` is one
  boolean - whether `@everyone` can view a channel - not the specific set of
  roles or members who can. Two *both*-restricted channels are always
  allowed to mirror into each other, even if their actual audiences do not
  overlap at all, because telling that apart needs
  `GET /channels/{id}/overwrites`, which is gated on `MANAGE_ROLES` *in that
  channel* - a permission this bot has no reason to hold. The one leak this
  card actually asked to close - a private channel's content reaching an
  open-to-everyone starboard - is fully covered; a narrower cross-restricted
  leak is not.
- **A jump link to the original message.** slim-m has no documented public
  URL scheme for one specific message, so the embed's footer names the
  origin channel instead of linking to it. `open_thread()`/`reply_to_id`
  exist for other purposes but neither one is "point at an arbitrary
  message in a different channel."
- **Un-starring.** If every reaction is removed, the count in the
  highlight's content drops to `⭐ **0**`, but the highlight itself stays up
  - only a delete of the original removes it. Many real starboards work
  this way on purpose: a highlight is a record that a message was once
  loved, not a live tally that can disappear.
- **New pins in the digest.** The weekly digest below is highlights only.
- **Un-highlighting a message that drops below the threshold on its own**
  (as opposed to being deleted) - same reasoning as un-starring above.

## The weekly digest

Optional, and genuinely cheap: `bot.background` runs a loop that sleeps a
week, then posts the top `DIGEST_TOP_N`-many (5) highlights from the last
week by their reaction count, or nothing at all if none crossed the
threshold in that window. It is a rolling week from
whenever the bot last started, not a calendar week - a bot that restarts
often will post the digest at a drifting time, which is an acceptable
tradeoff for how little code this is.
