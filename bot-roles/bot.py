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

Like `bot-ping/`, a frame type this does not recognise is ignored
rather than treated as an error, and the author is checked against `GET /me`
before ever answering - this bot posts in the very channel it listens to.

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
TRIGGER_GRANT = re.compile(r"^!role\s+(\S+)\s*$", re.IGNORECASE)
TRIGGER_LIST = re.compile(r"^!roles\s*$", re.IGNORECASE)

# Namespace for deriving a stable listing-message id from the channel id, so nothing needs to be persisted to disk.
LISTING_NAMESPACE = uuid.UUID("d1f6a9d0-0f0f-4b6a-9b0f-2f6b6f0f9a10")


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
    lines = ["**Self-service roles**", "`!role <name>` to add one, `!role remove <name>` to drop it."]
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


def escalation_explanation():
    return (
        "I can't do that: granting a role means granting whatever it carries, "
        "and I don't hold at least the same permissions myself right now. "
        "An admin needs to give me a role covering what I'm meant to hand out."
    )


def grant(client, role_name, actor_id, reply_to_id):
    role_id = ROLES.get(role_name)
    if role_id is None:
        client.send(CHANNEL, f"no role called `{role_name}` is offered here - try `!roles`.", reply_to_id=reply_to_id)
        return
    try:
        client.call("PUT", f"/members/{actor_id}/roles/{role_id}")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            client.send(CHANNEL, escalation_explanation(), reply_to_id=reply_to_id)
            return
        if err.code == 404:
            client.send(
                CHANNEL, f"`{role_name}` is misconfigured on my end - ask an admin to check it.", reply_to_id=reply_to_id
            )
            return
        raise
    client.send(CHANNEL, f"done - you have `{role_name}` now.", reply_to_id=reply_to_id)


def revoke(client, role_name, actor_id, reply_to_id):
    role_id = ROLES.get(role_name)
    if role_id is None:
        client.send(CHANNEL, f"no role called `{role_name}` is offered here - try `!roles`.", reply_to_id=reply_to_id)
        return
    try:
        client.call("DELETE", f"/members/{actor_id}/roles/{role_id}")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            client.send(CHANNEL, escalation_explanation(), reply_to_id=reply_to_id)
            return
        raise
    client.send(CHANNEL, f"removed `{role_name}`.", reply_to_id=reply_to_id)


def handle_message(client, me, authors, message):
    """The author check is what stops the bot answering itself, or any other
    bot, forever - see `slimbots.AuthorFilter`. It matters more here than in
    bot-ping: this bot posts its own listing in the very channel it listens
    to."""
    author_id = message.get("author_id")
    if not authors.should_handle(author_id, me):
        return
    content = (message.get("content") or "").strip()
    request_message_id = message.get("id")

    if match := TRIGGER_REMOVE.match(content):
        revoke(client, match.group(1), author_id, request_message_id)
        return
    if match := TRIGGER_GRANT.match(content):
        grant(client, match.group(1), author_id, request_message_id)
        return
    if TRIGGER_LIST.match(content):
        client.send(CHANNEL, listing_text(), reply_to_id=request_message_id)


def resync(client, cursor, authors):
    """Catches up on the configured channel over `/sync` when reconnecting
    mid-run, so a command sent during a dropped socket is not lost. `cursor`
    of `None` means this is the first connection this process has made, so
    there is nothing to replay - see the module docstring on why that gap is
    acceptable here but was not for bot-reminders."""
    me = client.me()["id"]
    if cursor is None:
        latest = client.call("GET", f"/channels/{CHANNEL}/messages?limit=1")
        return latest[0]["seq"] if latest else 0

    response = client.call("POST", "/sync", {"scopes": [{"channel_id": CHANNEL, "after_seq": cursor}]})
    scope = response["scopes"][0]
    for message in scope["messages"]:
        handle_message(client, me, authors, message)
    if scope["messages"]:
        return scope["messages"][-1]["seq"]
    if scope["reset"]:
        latest = client.call("GET", f"/channels/{CHANNEL}/messages?limit=1")
        return latest[0]["seq"] if latest else 0
    return cursor


async def attempt(client, state, authors, reset_delay):
    me = client.me()["id"]
    print(f"connected as {me}", flush=True)

    state["cursor"] = resync(client, state["cursor"], authors)
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
            handle_message(client, me, authors, message)
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
