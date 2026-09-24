#!/usr/bin/env python3
"""A slim-m bot in one file: it answers `!ping` with `pong`.

Run it with a bot token from Space settings -> Bots:

    pip install websockets
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... python3 bot.py

What it demonstrates is the whole shape of a bot, and nothing else:

1. a bot authenticates with `Authorization: Bearer <token>` like any client
2. it mints a single-use ticket from POST /auth/ws-ticket
3. it opens the WebSocket, says hello with that ticket, and reads events
4. it answers with an ordinary POST to the messages route

There is no bot-specific protocol anywhere in that list. A bot is a member with
a long-lived credential, so every call here is a call a person's client makes
too - which is the point of decision 0028 and the reason this file is short.

A frame type this does not recognise is ignored rather than treated as an error.
New event types get added over time, and a bot that dies on one it has never
heard of breaks on somebody else's upgrade.

Deliberately minimal, and these are the corners it cuts. It keeps no cursor, so
it only sees what arrives while it is connected rather than catching up on
restart; a bot that must not miss anything reads `seq` and calls /sync. It
reconnects with a flat delay rather than backing off. It answers every channel
it can see rather than being told which.

Two things are not corners cut, because a bot answering `!ping` forever or
dying ugly on a container restart is not a teaching point, it is a bug:

- **It never answers another bot, or a webhook.** `should_answer` checks the
  author against its own id, same as always, and also against
  `GET /users/{id}`'s `is_bot`/`is_webhook` fields, cached per id so a chatty
  channel does not refetch it every message. Several bots sharing one
  channel is exactly the setup where a `!ping` in another bot's own output
  would otherwise start an infinite reply loop - `slimbots.AuthorFilter`
  fixes this for every other template; this file writes the same handful of
  lines out inline instead of importing it, for the reason below.
- **SIGTERM exits cleanly.** A container orchestrator stops a bot with
  SIGTERM, not by pulling the plug, and the default Python behaviour for a
  frame this small a handler covers in full is worth having: log why, then
  exit 0 rather than whatever an unhandled signal does.

Every other template in this repo builds on the `slimbots` package
(`../slimbots/`) for this same plumbing. This one deliberately does not, so
there is always one file that shows the whole protocol with nothing hidden
behind an import - see `README.md` for why that stays true on purpose. The two
exceptions above are kept inline rather than imported for the same reason:
they are a few lines each, and a "teaching artifact" that answers itself
forever or dies badly on a restart is not teaching the right lesson.
"""

import asyncio
import json
import os
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

import websockets

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
PROTOCOL = 1
TRIGGER = "!ping"
REPLY = "pong"
# urllib's default UA is blocked by a CDN before it ever reaches slim-m.
USER_AGENT = "slimm-bot-ping/1.0"


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


def send(channel_id, content):
    """Post a message.

    The id is ours and makes the send idempotent within the channel, so a retry
    after an uncertain failure cannot double-post. Never vary the content between
    attempts under one id: the server replays what it first stored.
    """
    return call(
        "POST",
        f"/channels/{channel_id}/messages",
        {"id": str(uuid.uuid4()), "content": content},
    )


def socket_url(base):
    """The WebSocket URL for `base`, refusing to carry a token in plaintext.

    An https deployment becomes wss. Plain http is allowed only for a loopback
    address, because that is a developer running a server on their own machine;
    anywhere else it would put a long-lived bot credential on the wire in the
    clear, and a token is the one thing a bot cannot afford to leak.
    """
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


_is_bot_cache = {}


def is_bot_or_webhook(author_id):
    """Whether `author_id` is a bot or webhook account, cached after the
    first lookup so answering many messages from the same author costs one
    `GET /users/{id}` call, not one per message.

    slim-m's message frame carries no `author.bot`-style flag the way
    Discord's does, so this is resolved from the author's own profile
    instead - the same fix every other template in this repo gets from
    `slimbots.AuthorFilter`. This file stays free of that package on
    purpose (see the module docstring), so the same handful of lines is
    written out here instead of imported.
    """
    cached = _is_bot_cache.get(author_id)
    if cached is not None:
        return cached
    try:
        profile = call("GET", f"/users/{author_id}")
    except Exception as err:
        print(f"author lookup failed for {author_id}: {err}", file=sys.stderr)
        return False
    automated = bool(profile.get("is_bot")) or bool(profile.get("is_webhook"))
    _is_bot_cache[author_id] = automated
    return automated


def should_answer(message, me):
    """Whether this message is a `!ping` from a human other than us.

    The author check is what stops the bot answering itself, or another
    bot, forever. Every bot that posts in a channel it also listens to
    needs one, and with several bots sharing a channel that means every
    other bot too, not just ourselves.
    """
    author_id = message.get("author_id")
    if not author_id or author_id == me or is_bot_or_webhook(author_id):
        return False
    return (message.get("content") or "").strip().lower().startswith(TRIGGER)


async def listen():
    me = call("GET", "/me")["id"]
    print(f"connected as {me}", flush=True)

    ticket = call("POST", "/auth/ws-ticket")["ticket"]
    ws_url = socket_url(BASE)

    async with websockets.connect(
        ws_url, user_agent_header=USER_AGENT
    ) as socket:
        await socket.send(
            json.dumps({"type": "hello", "ticket": ticket, "protocol": PROTOCOL})
        )
        hello = json.loads(await socket.recv())
        if hello.get("type") != "hello":
            raise RuntimeError(f"expected a hello back, got {hello}")
        print("listening", flush=True)

        async for raw in socket:
            frame = json.loads(raw)
            # Ignore a frame type we do not know; see this module's docstring.
            if frame.get("type") != "message.created":
                continue
            message = frame.get("message") or {}
            if should_answer(message, me):
                send(frame["channel_id"], REPLY)
                print(f"answered in {frame['channel_id']}", flush=True)


def _install_sigterm_handler():
    """Exits 0 on SIGTERM instead of whatever an unhandled signal does, so a
    container stop reads as a clean shutdown in its own exit code."""

    def _on_sigterm(signum, frame):
        print("received SIGTERM, shutting down", flush=True)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _on_sigterm)


async def main():
    if not BASE or not TOKEN:
        print("set SLIMM_URL and SLIMM_BOT_TOKEN", file=sys.stderr)
        return 2
    _install_sigterm_handler()
    while True:
        try:
            await listen()
        except urllib.error.HTTPError as err:
            # 401 means the token was revoked; there is nothing to retry.
            if err.code == 401:
                print("token rejected - revoked?", file=sys.stderr)
                return 1
            print(f"http {err.code}, reconnecting", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, reconnecting", file=sys.stderr)
        await asyncio.sleep(3)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
