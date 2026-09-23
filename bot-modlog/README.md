# bot-modlog

A slim-m bot in one file that mirrors moderation actions into a channel:
timeouts, kicks, restores, role grants, role revokes, and role definition
changes.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_LOG_CHANNEL=<channel-uuid> \
python3 bot.py
```

`SLIMM_LOG_CHANNEL` is the one channel this bot posts into. It needs only
`VIEW_CHANNEL` and `SEND_MESSAGES` there - see "What your bot may do" in
`docs/bots/building-bots.md` for how to grant a bot a channel overwrite.

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

## The gap that cannot be closed

None of these five events carry a `seq`, so there is no cursor to persist
and no `/sync` scope that could ever replay one. Reproduced live: the bot
was killed, a timeout was applied and lifted and a role was created,
granted, revoked and deleted while it was down, and the bot was brought
back up. Its log shows nothing between the last event before the kill and
the first event after the restart - not a gap marker, not a "you may have
missed something," nothing. The one route that could answer "what happened
while I was gone" is `GET /reports/history`, gated behind `MANAGE_MESSAGES`.

This is different from `examples/bot-reminders/`'s reconnect story. There, a
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
- **Persist anything across a restart.** The name and role-name caches are
  in memory only and start cold again; see the module docstring.
- **Watch more than these five events.** Reactions, threads, polls, pins,
  and canvas activity all have their own event types and are out of scope
  here - see the other `examples/` bots and `docs/bots/building-bots.md`.
