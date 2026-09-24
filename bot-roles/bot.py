#!/usr/bin/env python3
"""A slim-m bot for self-service roles: `!role <name>` grants it, `!role
remove <name>` drops it, `!roles` lists what is on offer. At startup it also
posts (or updates) that same listing in its channel, so there is a visible
affordance even though nothing here is reaction-driven.

Run it with a bot token from Space settings -> Bots, the channel it should
watch, and a name-to-role-id map:

    pip install -r requirements.txt
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_CHANNEL=<channel-uuid> \\
        SLIMM_ROLES=member:<role-uuid>,helper:<role-uuid> python3 bot.py

Reaction roles - react to an emoji, get a role - is the obvious shape for
this and is not possible against slim-m today. The wire event for a reaction
change (`ReactionsChanged`) carries public counts only, never who reacted:
`crates/slimm-server/src/http/ws/frames.rs`'s `ReactionCountDto` says so in
its own doc comment, and decision 0009 explains why - a reactor a viewer has
blocked must not be visible to that viewer, so reactor identity is stripped
for everyone, not just the blocking case. There is no way to build reaction
roles on top of that, so this bot is command-driven instead. See the README
for the rest of what this deliberately does not do.

The single most important thing this demonstrates: `crates/slimm-server/src
/http/roles.rs` refuses to grant a role carrying a permission the actor does
not already hold. A role bot hands out permissions, so it must itself hold
at least what it hands out, or every grant comes back 403 - see the README's
"the no-escalation rule" section before assuming the bot is just broken.

Both `require_manage_roles` and the escalation guard return the identical
403 body ("insufficient permissions") - `crates/slimm-server/src/http/
error.rs` gives `ApiError::Forbidden` one message for every cause, so the
two failures are not distinguishable from the wire alone. This bot tells
them apart anyway, using what `GET /me` already hands it for free: its own
`permissions` bitmask says outright whether MANAGE_ROLES is held at all,
and if it is, `GET /roles` (now readable, since MANAGE_ROLES is confirmed)
names exactly which of the role's own bits it still lacks. `PERMISSION_BITS`
below mirrors `crates/slimm-server/src/permissions.rs` by hand - there is no
route that hands back permission names, only raw bits - and needs updating
if that file ever adds one.

Like `bot-ping/`, a frame type this does not recognise is ignored
rather than treated as an error, and the author is checked against `GET /me`
before ever answering - this bot posts in the very channel it listens to.
`slimbots.AuthorFilter` also keeps it from ever answering another bot in the
fleet.

Unlike `bot-reminders/`, there is no sqlite file. A reminder is a
promise to act in the future and must survive a restart or it silently never
fires; a role command is acted on immediately and, if lost, costs the member
nothing worse than typing it again. So the only state worth keeping is a
`seq` cursor to avoid re-reading old history, and that only needs to survive
a dropped websocket within one run, not a process restart - kept in memory
below rather than reaching for `slimbots.cursor`'s sqlite storage, which
would be the wrong tool for state this bot is fine losing. The README
explains this tradeoff further.

Auth, the REST call, the websocket handshake and the reconnect loop with
backoff come from the `slimbots` package (`../slimbots/`) - the same
plumbing every template but `bot-ping` shares.
"""

import asyncio
import os
import re
import sys
import urllib.error
import uuid

from slimbots import AuthorFilter, Client, Connection, is_not_found, run_forever

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLIMM_CHANNEL", "")
USER_AGENT = "slimm-bot-roles/1.0"

TRIGGER_REMOVE = re.compile(r"^!role\s+remove\s+(\S+)\s*$", re.IGNORECASE)
TRIGGER_MINE = re.compile(r"^!role\s+mine\s*$", re.IGNORECASE)
TRIGGER_GRANT = re.compile(r"^!role\s+(\S+)\s*$", re.IGNORECASE)
TRIGGER_LIST = re.compile(r"^!roles\s*$", re.IGNORECASE)
TRIGGER_STATUS = re.compile(r"^!roles\s+status\s*$", re.IGNORECASE)

# Namespace for deriving a stable listing-message id from the channel id, so nothing needs to be persisted to disk.
LISTING_NAMESPACE = uuid.UUID("d1f6a9d0-0f0f-4b6a-9b0f-2f6b6f0f9a10")

# Mirrors crates/slimm-server/src/permissions.rs by hand; see the module
# docstring for why - there is no route that turns a bit into its name.
PERMISSION_BITS = {
    "ADMINISTRATOR": 1 << 0,
    "VIEW_CHANNEL": 1 << 1,
    "SEND_MESSAGES": 1 << 2,
    "MANAGE_MESSAGES": 1 << 3,
    "MANAGE_CHANNELS": 1 << 4,
    "MANAGE_ROLES": 1 << 5,
    "KICK_MEMBERS": 1 << 6,
    "BAN_MEMBERS": 1 << 7,
    "CREATE_INVITE": 1 << 8,
    "ADD_REACTIONS": 1 << 9,
    "ATTACH_FILES": 1 << 10,
    "CONNECT": 1 << 11,
    "SPEAK": 1 << 12,
    "USE_CANVAS": 1 << 13,
    "MANAGE_CANVAS": 1 << 14,
    "MANAGE_SERVER": 1 << 15,
    "MENTION_EVERYONE": 1 << 16,
    "RUN_CODE": 1 << 17,
}

# role_id -> that role's own permission bits, once GET /roles has been readable at least once.
_role_permissions_cache = {}


def permission_names(bitmask):
    return sorted(name for name, bit in PERMISSION_BITS.items() if bitmask & bit)


def parse_roles(spec):
    """`"name:uuid,name:uuid"` -> an order-preserving `{name: role_id}`."""
    roles = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, _, role_id = pair.partition(":")
        if not name or not role_id:
            raise RuntimeError(f"bad SLIMM_ROLES entry: {pair!r}")
        roles[name.strip()] = role_id.strip()
    return roles


ROLES = parse_roles(os.environ.get("SLIMM_ROLES", ""))


def listing_message_id():
    return str(uuid.uuid5(LISTING_NAMESPACE, CHANNEL))


def listing_text():
    lines = [
        "**Self-service roles**",
        "`!role <name>` to add one, `!role remove <name>` to drop it, `!role mine` to see what you hold.",
    ]
    lines.append("")
    lines.extend(f"- `{name}`" for name in ROLES)
    return "\n".join(lines)


def post_listing(client):
    """Publishes the role listing at startup, editing the previous one in
    place when it already exists rather than posting a new copy every run."""
    message_id = listing_message_id()
    try:
        client.call("PATCH", f"/channels/{CHANNEL}/messages/{message_id}", {"content": listing_text()})
        print("updated the role listing", flush=True)
    except urllib.error.HTTPError as err:
        if not is_not_found(err):
            raise
        client.send(CHANNEL, listing_text(), message_id=message_id)
        print("posted the role listing", flush=True)


def fetch_role_permissions(client, role_id):
    """The configured role's own permission bits, or None if they cannot be
    read - either `GET /roles` itself needs MANAGE_ROLES (see the module
    docstring), or the role no longer exists. Cached, since roles rarely
    change mid-run; a mismatch after an admin edits one clears on restart."""
    if role_id in _role_permissions_cache:
        return _role_permissions_cache[role_id]
    try:
        roles = client.call("GET", "/roles")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            return None
        raise
    for role in roles:
        _role_permissions_cache[role["id"]] = role["permissions"]
    return _role_permissions_cache.get(role_id)


def escalation_explanation(client, my_permissions, role_name, role_id):
    """Names the exact permission gap rather than a generic refusal - see the
    module docstring for why the 403 alone cannot say which of the two
    guards actually fired."""
    if not (my_permissions & PERMISSION_BITS["MANAGE_ROLES"]):
        return (
            "I can't grant or remove any role here, not even a zero-permission "
            "one - I don't hold MANAGE_ROLES myself. An admin needs to grant "
            "this bot's own account MANAGE_ROLES before self-service roles can "
            "work at all."
        )
    role_permissions = fetch_role_permissions(client, role_id)
    if role_permissions is None:
        return (
            f"I hold MANAGE_ROLES but still can't grant `{role_name}` - either it "
            "was deleted, or something else is wrong. An admin should check it "
            "still exists."
        )
    missing = permission_names(role_permissions & ~my_permissions)
    if not missing:
        return (
            f"granting `{role_name}` was refused, but I hold everything it carries - "
            "an admin should check my role assignment did not just change."
        )
    return (
        f"I hold MANAGE_ROLES, but `{role_name}` also carries {', '.join(missing)}, "
        "which I don't hold myself. An admin needs to grant this bot's own "
        f"account {', '.join(missing)} before it can hand out `{role_name}`."
    )


def grant(client, my_permissions, role_name, actor_id, reply_to_id):
    role_id = ROLES.get(role_name)
    if role_id is None:
        client.send(CHANNEL, f"no role called `{role_name}` is offered here - try `!roles`.", reply_to_id=reply_to_id)
        return
    try:
        client.call("PUT", f"/members/{actor_id}/roles/{role_id}")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            client.send(CHANNEL, escalation_explanation(client, my_permissions, role_name, role_id), reply_to_id=reply_to_id)
            return
        if err.code == 404:
            client.send(
                CHANNEL, f"`{role_name}` is misconfigured on my end - ask an admin to check it.", reply_to_id=reply_to_id
            )
            return
        raise
    client.send(CHANNEL, f"done - you have `{role_name}` now.", reply_to_id=reply_to_id)


def revoke(client, my_permissions, role_name, actor_id, reply_to_id):
    role_id = ROLES.get(role_name)
    if role_id is None:
        client.send(CHANNEL, f"no role called `{role_name}` is offered here - try `!roles`.", reply_to_id=reply_to_id)
        return
    try:
        client.call("DELETE", f"/members/{actor_id}/roles/{role_id}")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            client.send(CHANNEL, escalation_explanation(client, my_permissions, role_name, role_id), reply_to_id=reply_to_id)
            return
        raise
    client.send(CHANNEL, f"removed `{role_name}`.", reply_to_id=reply_to_id)


def show_mine(client, actor_id, reply_to_id):
    try:
        user = client.call("GET", f"/users/{actor_id}")
    except urllib.error.HTTPError:
        client.send(CHANNEL, "could not look up your roles right now.", reply_to_id=reply_to_id)
        return
    held = {role_id for role_id in user.get("role_ids", [])}
    mine = [name for name, role_id in ROLES.items() if role_id in held]
    if mine:
        client.send(CHANNEL, f"you hold: {', '.join(mine)}", reply_to_id=reply_to_id)
    else:
        client.send(CHANNEL, "you hold none of the roles offered here.", reply_to_id=reply_to_id)


def show_status(client, my_permissions, reply_to_id):
    """`!roles status` - the same diagnosis `escalation_explanation` gives a
    failed grant, but on demand and for every configured role at once, so an
    admin can see the whole picture without provoking a 403 first."""
    lines = permission_names(my_permissions)
    lines = [f"I hold: {', '.join(lines) or 'nothing'}"]
    if not (my_permissions & PERMISSION_BITS["MANAGE_ROLES"]):
        lines.append("MANAGE_ROLES is missing, so no role here is grantable yet.")
        client.send(CHANNEL, "\n".join(lines), reply_to_id=reply_to_id)
        return
    for name, role_id in ROLES.items():
        role_permissions = fetch_role_permissions(client, role_id)
        if role_permissions is None:
            lines.append(f"`{name}`: cannot verify (role missing or unreadable)")
            continue
        missing = permission_names(role_permissions & ~my_permissions)
        lines.append(f"`{name}`: grantable" if not missing else f"`{name}`: missing {', '.join(missing)}")
    client.send(CHANNEL, "\n".join(lines), reply_to_id=reply_to_id)


def handle_message(client, me, my_permissions, authors, message):
    """The author check is what stops the bot answering itself forever, and
    matters more here than in bot-ping: this bot posts its own listing in
    the very channel it listens to. `authors.should_handle` also stops it
    answering another bot in the fleet the same way."""
    author_id = message.get("author_id")
    if not authors.should_handle(author_id, me):
        return
    content = (message.get("content") or "").strip()
    request_message_id = message.get("id")

    if TRIGGER_STATUS.match(content):
        show_status(client, my_permissions, request_message_id)
        return
    if TRIGGER_MINE.match(content):
        show_mine(client, author_id, request_message_id)
        return
    if match := TRIGGER_REMOVE.match(content):
        revoke(client, my_permissions, match.group(1), author_id, request_message_id)
        return
    if match := TRIGGER_GRANT.match(content):
        grant(client, my_permissions, match.group(1), author_id, request_message_id)
        return
    if TRIGGER_LIST.match(content):
        client.send(CHANNEL, listing_text(), reply_to_id=request_message_id)


def resync(client, me, my_permissions, authors, cursor):
    """Catches up on the configured channel over `/sync` when reconnecting
    mid-run, so a command sent during a dropped socket is not lost. `cursor`
    of `None` means this is the first connection this process has made, so
    there is nothing to replay - see the module docstring on why that gap is
    acceptable here but was not for bot-reminders."""
    if cursor is None:
        latest = client.call("GET", f"/channels/{CHANNEL}/messages?limit=1")
        return latest[0]["seq"] if latest else 0

    response = client.call("POST", "/sync", {"scopes": [{"channel_id": CHANNEL, "after_seq": cursor}]})
    scope = response["scopes"][0]
    for message in scope["messages"]:
        handle_message(client, me, my_permissions, authors, message)
    if scope["messages"]:
        return scope["messages"][-1]["seq"]
    if scope["reset"]:
        latest = client.call("GET", f"/channels/{CHANNEL}/messages?limit=1")
        return latest[0]["seq"] if latest else 0
    return cursor


def log_startup_diagnosis(client, my_permissions):
    """Prints, once per connect, exactly what this bot could and could not
    grant right now - the same computation `!roles status` runs on demand,
    surfaced in the logs too so an operator does not have to ask the bot
    to find out."""
    print(f"my permissions: {', '.join(permission_names(my_permissions)) or 'none'}", flush=True)
    if not (my_permissions & PERMISSION_BITS["MANAGE_ROLES"]):
        print("MANAGE_ROLES is missing - no configured role is grantable yet", flush=True)
        return
    for name, role_id in ROLES.items():
        role_permissions = fetch_role_permissions(client, role_id)
        if role_permissions is None:
            print(f"  {name}: cannot verify (role missing or unreadable)", flush=True)
            continue
        missing = permission_names(role_permissions & ~my_permissions)
        print(f"  {name}: grantable" if not missing else f"  {name}: missing {', '.join(missing)}", flush=True)


async def attempt(client, state, authors, reset_delay):
    me_info = client.me()
    me = me_info["id"]
    my_permissions = me_info.get("permissions", 0)
    print(f"connected as {me}", flush=True)
    log_startup_diagnosis(client, my_permissions)

    state["cursor"] = resync(client, me, my_permissions, authors, state["cursor"])
    post_listing(client)

    async with await Connection.open(client) as socket:
        print("listening", flush=True)
        reset_delay()

        async for frame in socket.frames():
            # Ignore a frame type we do not know; see bot-ping's docstring.
            if frame.get("type") != "message.created":
                continue
            if frame.get("channel_id") != CHANNEL:
                continue
            message = frame.get("message") or {}
            handle_message(client, me, my_permissions, authors, message)
            seq = message.get("seq")
            if seq is not None:
                state["cursor"] = seq


async def main():
    if not BASE or not TOKEN or not CHANNEL or not ROLES:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN, SLIMM_CHANNEL and SLIMM_ROLES", file=sys.stderr)
        return 2

    client = Client(BASE, TOKEN, USER_AGENT)
    authors = AuthorFilter(client)
    state = {"cursor": None}

    return await run_forever(lambda reset_delay: attempt(client, state, authors, reset_delay))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
