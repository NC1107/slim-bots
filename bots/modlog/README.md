# modlog

A slim-m bot that mirrors moderation actions into a channel: timeouts,
kicks, restores, role grants, role revokes, and role definition changes.

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`. The
five moderation events map onto `@bot.event` handlers (`on_member_timeout`,
`on_member_removed`, `on_member_restored`, `on_member_role_changed`,
`on_role_changed`) instead of a manual frame-type dispatch, `!modlog` is
one `@bot.command` instead of a trigger regex per subcommand, and `Bot`
itself owns `SLIMM_URL`/`SLIMM_BOT_TOKEN`/`SLIMM_CHANNELS` and the seq
cursor - this script only opens its own tables at `bot.data_path` (from
`SLIMM_DB_PATH`), never importing `os` itself. `bot-ping` stays free of
the library on purpose; see its own README.

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
`SLIMM_DB_PATH` (default `modlog.db`) holds this bot's own local transcript,
reconnect-gap history, and (shared with `Bot`) the seq cursor for its own
command traffic - never a substitute for `GET /reports/history`.

## Commands

- `!modlog stats` - counts of every event this bot has logged locally, by
  kind, since its database was created. Explicitly labelled as this bot's
  own transcript, not an audit trail.
- `!modlog gaps` - the last several reconnect gaps this bot noticed, each a
  permanent hole in the log rather than something a later event fills in.
- `!modlog permissions` - exactly what `MANAGE_MESSAGES` and `MANAGE_ROLES`
  would each add, on demand.

## What this proves about permissions

This bot was run live holding none of `MANAGE_ROLES`, `MANAGE_MESSAGES`,
`KICK_MEMBERS` or `BAN_MEMBERS`. It still logged, in real time:

- a member being timed out and the timeout being lifted
- a member being removed from the Space and being let back in
- a role being granted to a member and revoked from them
- a role being created and deleted

`crates/slimm-server/src/http/ws/authorization.rs` delivers
`member.timeout`, `member.removed`, `member.restored`, `member.role_changed`
and `role.changed` unconditionally to every connected session - there is no
permission check on any of the five. Holding a channel overwrite to post
the log was the only bit this bot needed.

The trade is that three routes this bot might have wanted stayed closed the
whole time, confirmed live with 403s against the running deployment:

- `GET /roles` (needs `MANAGE_ROLES`) - the only way to list every role by
  id, including one nobody currently holds
- `GET /reports/history` (needs `MANAGE_MESSAGES`) - the moderation-history
  feed, and the only thing that could ever answer "what did I miss"
- `GET /members/removed` (needs `BAN_MEMBERS`) - the current removal list

None of these were requested for this bot. The point of the example is
showing what a permission-poor bot can and cannot do, not asking for more.

## The two things the wire events do not say

- **`member.role_changed` carries no direction.** The event says a role and
  a member changed, never whether it was a grant or a revoke. This bot
  fetches the member's current role list and diffs it against what it last
  saw to work that out. See the module docstring for the one case that
  still comes out ambiguous.
- **A role's name is not always resolvable without `MANAGE_ROLES`.** This
  bot learns role names as a side effect of looking up members, which
  covers a role with a member holding it, but not a role nobody visible to
  it has ever held. Confirmed live: a role's own `role.changed` event for a
  role this bot had not yet seen granted to anyone logs as a bare id, with
  the reason stated in the log line itself, not silently.

## The gap that cannot be closed - now visible, not just documented

None of these five events carry a `seq`, so there is no cursor to persist
and no `/sync` scope that could ever replay one. A moderation action during
a dropped socket is gone from the log for good - `GET /reports/history`,
gated behind `MANAGE_MESSAGES`, is the only route that could ever answer
"what happened while I was gone."

What changed: this bot used to note that fact once, to its own stdout, at
startup. Now, on every reconnect after a previous successful connection
(never the very first connect, which is not a gap), it posts how long it
was offline right in the log and records it for `!modlog gaps` to answer
later. The downtime estimate runs from the last frame this bot actually saw
(via the framework's `on_frame`, which fires for any frame at all, not just
the five watched types) to the moment the new connection is confirmed live -
a decent estimate, never exact, since nothing on the wire says precisely
when the drop happened.

This is different from `bots/reminders/`'s reconnect story. There, a
dropped socket is invisible to the *bot* precisely because `seq` and
`/sync` make it invisible - the feature never notices. Here it is invisible
to the bot too, but there is no later event that could ever fill the hole
in. A moderation log built on these events is a live feed with a silent,
permanent gap on every reconnect, not an eventually-consistent one. A
deployment that needs a true audit trail should read
`GET /reports/history` with a moderator's own credential, not trust a
bot's transcript of what it happened to be connected for.

## A found edge case: logging your own timeout

Timing this bot's own account out makes its very next log post - the one
reporting that timeout - come back `403`, because a timeout blocks
`SEND_MESSAGES` immediately, before the member finds out any other way.
Reproduced live. The event handler catches this per-frame instead of
letting it kill the socket: a forced reconnect here would be strictly
worse, given the gap above has no way to recover what happens next.

## A platform finding, not a bot one: restoring a removed bot does not restore it

Not something this bot works around, but found while proving `member.removed`
and `member.restored` live and worth carrying back: removing a bot from the
Space and then restoring it does not give the bot a working credential
again, even though `member.restored` fires and the bot reappears in the
member list.

`PUT /members/{id}/removal` revokes every session row for that user
(`crates/slimm-server/src/store/removals.rs`), and a bot's token is only
valid while its one backing session is unrevoked
(`crates/slimm-server/src/store/bots.rs::authenticate_bot` joins on
`s.revoked_at IS NULL`). `DELETE /members/{id}/removal` only deletes the
`space_removals` row; it never touches that session. A human sidesteps this
by signing in again, which mints a fresh session - a bot has no sign-in to
retry, so its original token is dead for good.

Reproduced live against a throwaway bot created for this purpose (not
`sample_bot`, to avoid touching the credential this example itself needed):
`GET /me` with its token returned 401 immediately after removal, and still
returned 401 after restoring it, with nothing in between to explain why.
The only recovery is minting the bot a new token from Space settings.
Whether that is worth fixing - restoring a session on `DELETE
/members/{id}/removal` for a bot specifically - is a call for whoever owns
decision 0028, not something to route around here.

## What this deliberately does not do

- **Say why.** A timeout or removal's reason lives in
  `moderation_audit_log`, reachable only via `GET /reports/history`
  (`MANAGE_MESSAGES`). This bot logs who and when, never why.
- **Distinguish a role create from a rename from a permission edit from a
  delete.** `role.changed` fires for all four and says only the id. Naming
  which one happened would need `GET /roles` (`MANAGE_ROLES`) plus a
  before/after diff, and this bot does not hold that permission on purpose.
- **Persist the name and role-name caches.** Only `events` and `gaps` live
  in `SLIMM_DB_PATH`; the in-memory lookups are cheap to rebuild from the
  next few events and simply start cold again after a restart.
- **Watch more than these five events.** Reactions, threads, polls, pins,
  and canvas activity all have their own event types and are out of scope
  here - see the other bots here and slim-m's `docs/bots/building-bots.md`.

## Output

Every log line's `content` is still the plain-text sentence a reader can
skim; a real `Embed` (footer only, naming the event kind, e.g.
`member.removed`) rides alongside it, for a client that wants to group or
filter the feed by kind without parsing the sentence. See
`../../docs/framework.md`'s embeds section for the fallback an older server
gets instead.
