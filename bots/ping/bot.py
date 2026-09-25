#!/usr/bin/env python3
"""The whole slim-m bot protocol in one file, nothing hidden behind an import; see README.md."""

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
    """Posts a message under a fresh id, making a retry after an uncertain failure safe."""
    return call(
        "POST",
        f"/channels/{channel_id}/messages",
        {"id": str(uuid.uuid4()), "content": content},
    )


def socket_url(base):
    """The WebSocket URL for `base`, refusing to carry a token in plaintext outside loopback."""
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
    """Whether `author_id` is automated, cached per id; see README.md."""
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
    """Whether this message is a `!ping` from a human other than us."""
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
            # Ignore a frame type we do not know; see README.md.
            if frame.get("type") != "message.created":
                continue
            message = frame.get("message") or {}
            if should_answer(message, me):
                send(frame["channel_id"], REPLY)
                print(f"answered in {frame['channel_id']}", flush=True)


def _install_sigterm_handler():
    """Exits 0 on SIGTERM instead of an unhandled signal's default, so a container stop is a clean shutdown."""

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
