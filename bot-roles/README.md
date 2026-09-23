# bot-roles

A slim-m bot in one file: `!role <name>` to grab a self-service role,
`!role remove <name>` to drop it, `!roles` to see what is on offer. At
startup it also posts (or updates) that same listing in its channel.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNEL=<channel-uuid> \
SLIMM_ROLES=member:<role-uuid>,helper:<role-uuid> \
python3 bot.py
```

`SLIMM_ROLES` is a comma-separated `name:role-id` map. Only the roles listed
there are offered; a request for anything else is refused with a message
that says so, never ignored silently. `SLIMM_CHANNEL` is the one channel the
bot watches and posts in - see "Getting a token" and "What your bot may do"
in `docs/bots/building-bots.md` for how to find channel and role ids, and
how to grant the bot `SEND_MESSAGES`/`VIEW_CHANNEL` there.

## The no-escalation rule

`crates/slimm-server/src/http/roles.rs` refuses to grant a role that carries
a permission the actor does not already hold themselves. That check applies
to this bot exactly the way it applies to a person: holding `MANAGE_ROLES`
is not enough on its own, the bot must also already hold at least whatever
permissions the roles it hands out carry.

Concretely, this means:

- If a role in `SLIMM_ROLES` carries no extra permissions (a plain "member"
  or "notified" tag with `permissions: 0`), the bot only needs `MANAGE_ROLES`
  itself, since granting `0` extra permissions can never escalate anyone.
- If a role carries real permissions (say a "helper" role with
  `MANAGE_MESSAGES`), the bot needs `MANAGE_MESSAGES` too, or every grant of
  that role comes back `403` and the member sees a plain refusal explaining
  why rather than the bot quietly doing nothing.

This is deliberate on the platform's side, not a bug to route around. A
deployment that wants this bot to hand out a role with real permissions has
to give the bot at least that much itself. If a grant is coming back
forbidden, check the bot's own roles before assuming the code is broken.

## Do not answer yourself

Like every slim-m bot, this one checks the message author against its own
id from `GET /me` before acting - see "Answering yourself" in
`docs/bots/building-bots.md`. It matters more here than in `bot-ping`: this
bot posts its own role listing in the very channel it listens to, so
skipping that check would have it react to its own listing message forever.

## What this deliberately does not do

- **Reaction roles.** React to an emoji, get a role, is the obvious shape
  for this and is impossible against slim-m today, on purpose. The wire
  event for a reaction change (`ReactionsChanged`) carries public counts
  only - `ReactionCountDto` in `crates/slimm-server/src/http/ws/frames.rs`
  says in its own doc comment that what a user reacted with is never
  broadcast. Decision 0009 explains why: a reactor someone has blocked must
  not be visible to that someone, so reactor identity is stripped for
  everyone, not just the blocking relationship. There is no reactor id on
  the wire to build reaction roles from, so this bot is command-driven
  instead. This was checked twice already, once statically against the
  frame types and once live against a running deployment - it is a platform
  property, not a missing feature waiting on this example.
- **Durable state.** Unlike `examples/bot-reminders/`, there is no sqlite
  file here. A reminder is a promise to act later and must survive a
  restart, or it silently never fires. A role command is acted on
  immediately: if the bot is offline when it arrives, the member's cost is
  typing it again, not a promise broken without them knowing. So the only
  state worth keeping is a `seq` cursor to skip old channel history, and
  that only needs to survive a dropped websocket within one run (handled
  in memory, with `/sync` catching up a reconnect), not a process restart.
  A full restart just re-baselines at the channel's current head, the same
  tradeoff `bot-reminders` accepts for its own long-outage case. The
  listing message's id is derived deterministically from the channel id
  (a UUIDv5), so even that needs nothing persisted to stay stable across
  restarts.
- **Re-granting a role a moderator took away by hand.** This bot only ever
  touches a member's roles in direct response to a `!role`/`!role remove`
  command. There is no background pass that walks members and reconciles
  their roles against some expected state, so a moderator revoking a role
  by hand stays revoked until the member asks for it again themselves.
- **Answering outside `SLIMM_CHANNEL`.** Only one channel is configured on
  purpose; every other channel the bot's role can see is left alone.
- **Editing a pending request, role hierarchies, or approval flows.** This
  is a flat, self-service list. A role that should require approval before
  it is handed out is a different, larger bot.
