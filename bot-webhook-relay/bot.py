#!/usr/bin/env python3
"""A tiny HTTP server that turns a POSTed JSON body into a slim-m message -
the shape of thing you point Sonarr, Uptime Kuma, Grafana or a GitHub Actions
step at, the way you would point them at a Discord incoming webhook.

slim-m does not have real incoming webhooks yet - see
`docs/decisions/0030-incoming-webhooks.md` in the slim-m repo, which designs
one but has not built it. This bot is the stand-in until that ships: it is a
program you run and point tools at, not a URL slim-m itself hands out, so it
is a bot in decision 0028's sense, not a webhook in 0030's. Read both records
before building on this - the gaps below are exactly the ones 0030 exists to
close properly.

Run it with a bot token from Space settings -> Bots and the channel it
should post into:

    pip install -r requirements.txt
    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_CHANNEL=<channel-uuid> python3 bot.py

Then point a tool at `http://<this-machine>:8099/post` (`WEBHOOK_PORT` and
`WEBHOOK_PATH` to change either). A request looks like:

    POST /post
    { "content": "backup finished: 4.2 GiB in 3m12s" }

`username`, if a tool sends one, is honoured the same narrow way decision
0030 designs for a real webhook: a label, never an identity. This bot has no
principal of its own to post as - it posts as itself, `SLIMM_BOT_TOKEN` - so
the label is folded into the text as `**{username}:** {content}` rather than
replacing the author, which stays this bot for every message regardless of
what a caller claims. That is the honest difference from 0030's design and
the whole reason this is a stopgap: a real webhook gets its own principal
and its own `Webhook` badge; this one is always this bot, badged `Bot`.

## Idempotency

A tool's own retry-after-timeout should not double-post, and a heartbeat
tool legitimately POSTs a byte-identical body on a schedule and must not be
silently deduplicated against itself - the same two-sided rule
docs/decisions/0030-incoming-webhooks.md lands on for the real thing.

So: an `Idempotency-Key` request header, if a caller sends one, is hashed
into a deterministic message id, and a retried request with the same key and
the same body returns the message already stored rather than posting twice
(the server's own `id`-keyed idempotency, see "Sending a message" in
docs/bots/building-bots.md). A caller that sends no key gets a fresh id
every request - at-least-once delivery, and a duplicate on a genuine retry
reads as noise, not a defect, exactly as 0030 accepts for the real webhook
route.

## What this deliberately does not do

- **Discord's payload shape.** `content` and `username` only - no `embeds`,
  no `allowed_mentions`, no `tts`, none of the fields decision 0030 spends a
  whole section on accepting-but-discarding for real compatibility. A tool
  that only speaks the full Discord shape will have its `embeds` silently
  ignored, same as slim-m's own future webhook route promises for a field it
  does not honour - but this bot promises much less of that shape overall,
  since it is not trying to be the compatibility layer 0030 is.
- **A shared secret is optional, not required.** Set `WEBHOOK_SHARED_SECRET`
  and every request needs an `X-Webhook-Secret` header matching it, checked
  with `hmac.compare_digest` rather than `==` so a wrong guess cannot be
  timed byte by byte. Unset, anyone who can reach the port can post. This is
  meant to run on a LAN or behind a reverse proxy you control - it is not
  hardened against the open internet the way a real webhook token is (0030's
  32-byte `generate_secret` token, checked at the framework's own
  constant-time layer).
- **Per-caller rate limiting.** A flood here is bounded only by slim-m's own
  bot rate limit on the send itself (`Client.send`'s retry backs off a 429,
  it does not refuse the flood at the door) - decision 0030's whole
  `Class::Webhook`, sized against push-amplification cost, has no equivalent
  here. Run this behind a reverse proxy if that matters to you.
- **TLS.** This is a plain `http.server`. Put it behind a reverse proxy
  (Caddy, nginx) if it needs to leave a LAN, the same way slim-m's own
  deployment guide puts the server behind one.
- **Routing to more than one channel.** One process, one `SLIMM_CHANNEL`.
  Run a second instance, on a second port, for a second channel.
- **Attachments or embeds of any kind.** Text only. A tool that wants a
  screenshot has nowhere to put one here.
"""

import hashlib
import hmac
import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from slimbots import Client, is_token_revoked

BASE = os.environ.get("SLIMM_URL", "").rstrip("/")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLIMM_CHANNEL", "")
HOST = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEBHOOK_PORT", "8099"))
PATH = os.environ.get("WEBHOOK_PATH", "/post")
SHARED_SECRET = os.environ.get("WEBHOOK_SHARED_SECRET", "")
MAX_BODY_BYTES = 32_768
USER_AGENT = "slimm-bot-webhook-relay/1.0"
# Fixed, so the same (Idempotency-Key, body) maps to the same id across restarts; see "Idempotency" above.
NAMESPACE = uuid.UUID("736c696d-6d62-6f74-7765-626861637475")


def message_id_for(idempotency_key, raw_body):
    if not idempotency_key:
        return str(uuid.uuid4())
    digest = hashlib.sha256(idempotency_key.encode() + b"\0" + raw_body).hexdigest()
    return str(uuid.uuid5(NAMESPACE, digest))


def render_content(payload):
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    username = payload.get("username")
    if isinstance(username, str) and username.strip():
        return f"**{username.strip()}:** {content}"
    return content


class Handler(BaseHTTPRequestHandler):
    server_version = "slimm-bot-webhook-relay/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr)

    def _reply(self, code, body=None):
        self.send_response(code)
        if body is None:
            self.send_header("content-length", "0")
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _secret_ok(self):
        if not SHARED_SECRET:
            return True
        given = self.headers.get("X-Webhook-Secret", "")
        return hmac.compare_digest(given, SHARED_SECRET)

    # BaseHTTPRequestHandler's own naming, not this file's.
    def do_POST(self):  # noqa: N802
        if self.path != PATH:
            self._reply(404)
            return
        if not self._secret_ok():
            self._reply(401, {"error": "bad or missing X-Webhook-Secret"})
            return
        length = int(self.headers.get("content-length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(400, {"error": f"body must be 1..{MAX_BODY_BYTES} bytes"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._reply(400, {"error": "body must be JSON"})
            return
        if not isinstance(payload, dict):
            self._reply(400, {"error": "body must be a JSON object"})
            return
        content = render_content(payload)
        if content is None:
            self._reply(400, {"error": "content must be a non-empty string"})
            return
        if len(content) > 4000:
            self._reply(400, {"error": "content too long (4000 chars max)"})
            return

        message_id = message_id_for(self.headers.get("Idempotency-Key"), raw)
        try:
            self.server.client.send(CHANNEL, content, message_id=message_id)
        except Exception as err:  # noqa: BLE001
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise SystemExit(1)
            print(f"send failed: {type(err).__name__}: {err}", file=sys.stderr)
            self._reply(502, {"error": "could not reach slim-m"})
            return
        self._reply(204)


def check_config():
    missing = [n for n, v in (("SLIMM_URL", BASE), ("SLIMM_BOT_TOKEN", TOKEN), ("SLIMM_CHANNEL", CHANNEL)) if not v]
    if missing:
        print(f"missing required environment: {', '.join(missing)}", file=sys.stderr)
        return False
    return True


def main():
    if not check_config():
        return 2

    client = Client(BASE, TOKEN, USER_AGENT)
    me = client.me()
    print(f"connected as {me['id']}", flush=True)

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.client = client
    secret_note = "a shared secret is required" if SHARED_SECRET else "no shared secret set - anyone reaching this port can post"
    # No scheme printed: it is plain http by design (see "TLS" in the README).
    print(f"listening on {HOST}:{PORT}{PATH} - {secret_note}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
