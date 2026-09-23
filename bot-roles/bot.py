#!/usr/bin/env python3
"""A slim-m bot for self-service roles: `!role <name>` grants it, `!role
remove <name>` drops it, `!roles` lists what is on offer. At startup it also
posts (or updates) that same listing in its channel, so there is a visible
affordance even though nothing here is reaction-driven.

Run it with a bot token from Space settings -> Bots, the channel it should
watch, and a name-to-role-id map:

    pip install websockets
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

Like `examples/bot-ping/`, a frame type this does not recognise is ignored
rather than treated as an error, and the author is checked against `GET /me`
before ever answering - this bot posts in the very channel it listens to.

Unlike `examples/bot-reminders/`, there is no sqlite file. A reminder is a
promise to act in the future and must survive a restart or it silently never
fires; a role command is acted on immediately and, if lost, costs the member
nothing worse than typing it again. So the only state worth keeping is a
`seq` cursor to avoid re-reading old history, and that only needs to survive
a dropped websocket within one run, not a process restart - kept in memory
below. The README explains this tradeoff further.
"""

import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

import websockets

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLIMM_CHANNEL", "")
PROTOCOL = 1
MAX_BACKOFF_SECONDS = 60
# urllib's default UA is blocked by a CDN before it ever reaches slim-m.
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


def call(method, path, body=None):
    """One authenticated REST call, returning parsed JSON or None for 204."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("authorization", f"Bearer {TOKEN}")
    request.add_header("user-agent", USER_AGENT)
    if data is not None:
        request.add_header("content-type", "application/json")
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def send(content, message_id=None, reply_to_id=None):
    """Post a message in the configured channel, idempotent on `message_id`
    so a retry after an uncertain send cannot double-post."""
    body = {"id": message_id or str(uuid.uuid4()), "content": content}
    if reply_to_id:
        body["reply_to_id"] = reply_to_id
    return call("POST", f"/channels/{CHANNEL}/messages", body)


def socket_url(base):
    """The WebSocket URL for `base`; see bot-ping's identical helper for why
    plaintext ws is refused off loopback."""
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == "https":
        return urllib.parse.urlunsplit(("wss", parts.netloc, "/ws", "", ""))
    if parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1"):
        print("warning: plaintext ws, loopback only", file=sys.stderr)
        return urllib.parse.urlunsplit(("ws", parts.netloc, "/ws", "", ""))
    raise RuntimeError(
        f"refusing to send a bot token over {parts.scheme or 'no'} scheme to "
        f"{parts.hostname or base}; use https"
    )


def listing_message_id():
    return str(uuid.uuid5(LISTING_NAMESPACE, CHANNEL))


def listing_text():
    lines = ["**Self-service roles**", "`!role <name>` to add one, `!role remove <name>` to drop it."]
    lines.append("")
    lines.extend(f"- `{name}`" for name in ROLES)
    return "\n".join(lines)


def post_listing():
    """Publishes the role listing at startup, editing the previous one in
    place when it already exists rather than posting a new copy every run."""
    message_id = listing_message_id()
    try:
        call("PATCH", f"/channels/{CHANNEL}/messages/{message_id}", {"content": listing_text()})
        print("updated the role listing", flush=True)
    except urllib.error.HTTPError as err:
        if err.code != 404:
            raise
        send(listing_text(), message_id=message_id)
        print("posted the role listing", flush=True)


def escalation_explanation():
    return (
        "I can't do that: granting a role means granting whatever it carries, "
        "and I don't hold at least the same permissions myself right now. "
        "An admin needs to give me a role covering what I'm meant to hand out."
    )


def grant(role_name, actor_id, reply_to_id):
    role_id = ROLES.get(role_name)
    if role_id is None:
        send(f"no role called `{role_name}` is offered here - try `!roles`.", reply_to_id=reply_to_id)
        return
    try:
        call("PUT", f"/members/{actor_id}/roles/{role_id}")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            send(escalation_explanation(), reply_to_id=reply_to_id)
            return
        if err.code == 404:
            send(f"`{role_name}` is misconfigured on my end - ask an admin to check it.", reply_to_id=reply_to_id)
            return
        raise
    send(f"done - you have `{role_name}` now.", reply_to_id=reply_to_id)


def revoke(role_name, actor_id, reply_to_id):
    role_id = ROLES.get(role_name)
    if role_id is None:
        send(f"no role called `{role_name}` is offered here - try `!roles`.", reply_to_id=reply_to_id)
        return
    try:
        call("DELETE", f"/members/{actor_id}/roles/{role_id}")
    except urllib.error.HTTPError as err:
        if err.code == 403:
            send(escalation_explanation(), reply_to_id=reply_to_id)
            return
        raise
    send(f"removed `{role_name}`.", reply_to_id=reply_to_id)


def handle_message(me, message):
    """The author check is what stops the bot answering itself forever, and
    matters more here than in bot-ping: this bot posts its own listing in
    the very channel it listens to."""
    author_id = message.get("author_id")
    if author_id is None or author_id == me:
        return
    content = (message.get("content") or "").strip()
    request_message_id = message.get("id")

    if match := TRIGGER_REMOVE.match(content):
        revoke(match.group(1), author_id, request_message_id)
        return
    if match := TRIGGER_GRANT.match(content):
        grant(match.group(1), author_id, request_message_id)
        return
    if TRIGGER_LIST.match(content):
        send(listing_text(), reply_to_id=request_message_id)


def resync(cursor):
    """Catches up on the configured channel over `/sync` when reconnecting
    mid-run, so a command sent during a dropped socket is not lost. `cursor`
    of `None` means this is the first connection this process has made, so
    there is nothing to replay - see the module docstring on why that gap is
    acceptable here but was not for bot-reminders."""
    me = call("GET", "/me")["id"]
    if cursor is None:
        latest = call("GET", f"/channels/{CHANNEL}/messages?limit=1")
        return latest[0]["seq"] if latest else 0

    response = call("POST", "/sync", {"scopes": [{"channel_id": CHANNEL, "after_seq": cursor}]})
    scope = response["scopes"][0]
    for message in scope["messages"]:
        handle_message(me, message)
    if scope["messages"]:
        return scope["messages"][-1]["seq"]
    if scope["reset"]:
        latest = call("GET", f"/channels/{CHANNEL}/messages?limit=1")
        return latest[0]["seq"] if latest else 0
    return cursor


async def listen(cursor, on_connected):
    me = call("GET", "/me")["id"]
    print(f"connected as {me}", flush=True)

    cursor = resync(cursor)
    post_listing()

    ticket = call("POST", "/auth/ws-ticket")["ticket"]
    ws_url = socket_url(BASE)

    async with websockets.connect(ws_url, user_agent_header=USER_AGENT) as socket:
        await socket.send(json.dumps({"type": "hello", "ticket": ticket, "protocol": PROTOCOL}))
        hello = json.loads(await socket.recv())
        if hello.get("type") != "hello":
            raise RuntimeError(f"expected a hello back, got {hello}")
        print("listening", flush=True)
        on_connected()

        async for raw in socket:
            frame = json.loads(raw)
            # Ignore a frame type we do not know; see bot-ping's docstring.
            if frame.get("type") != "message.created":
                continue
            if frame.get("channel_id") != CHANNEL:
                continue
            message = frame.get("message") or {}
            handle_message(me, message)
            seq = message.get("seq")
            if seq is not None:
                cursor = seq
    return cursor


async def main():
    if not BASE or not TOKEN or not CHANNEL or not ROLES:
        print("set SLIMM_URL, SLIMM_BOT_TOKEN, SLIMM_CHANNEL and SLIMM_ROLES", file=sys.stderr)
        return 2

    cursor = None
    delay = 1

    def reset_delay():
        nonlocal delay
        delay = 1

    while True:
        try:
            cursor = await listen(cursor, reset_delay)
        except urllib.error.HTTPError as err:
            # 401 means the token was revoked; there is nothing to retry.
            if err.code == 401:
                print("token rejected - revoked?", file=sys.stderr)
                return 1
            print(f"http {err.code}, retrying in {delay}s", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying in {delay}s", file=sys.stderr)
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_BACKOFF_SECONDS)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
