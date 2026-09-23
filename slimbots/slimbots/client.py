"""The REST side of a bot: one authenticated call, and an idempotent send."""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .retry import call_with_retry

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def socket_url(base):
    """The WebSocket URL for `base`, refusing to carry a token in plaintext.

    An https deployment becomes wss. Plain http is allowed only for a
    loopback address, because that is a developer running a server on their
    own machine; anywhere else it would put a long-lived bot token on the
    wire in the clear, and a token is the one thing a bot cannot afford to
    leak.
    """
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == "https":
        return urllib.parse.urlunsplit(("wss", parts.netloc, "/ws", "", ""))
    if parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS:
        print("warning: plaintext ws, loopback only", file=sys.stderr)
        return urllib.parse.urlunsplit(("ws", parts.netloc, "/ws", "", ""))
    raise RuntimeError(
        f"refusing to send a bot token over {parts.scheme or 'no'} scheme to "
        f"{parts.hostname or base}; use https"
    )


class Client:
    """An authenticated slim-m REST client for one bot token.

    `call` is the general-purpose escape hatch: it makes no assumptions
    about the route, so a template reaches for it directly for anything this
    class does not name explicitly - `/sync`, canvas routes, `/users/{id}`,
    whatever the bot needs. `send`, `me` and `ws_ticket` exist only because
    every template calls them, byte for byte.
    """

    def __init__(self, base, token, user_agent):
        if not base or not token:
            raise ValueError("a Client needs both a base URL and a token")
        self.base = base.rstrip("/")
        self.token = token
        self.user_agent = user_agent

    def call(self, method, path, body=None):
        """One authenticated REST call, returning parsed JSON or None for 204."""
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        request.add_header("authorization", f"Bearer {self.token}")
        # A CDN can reject urllib's default UA; see docs/bots/building-bots.md.
        request.add_header("user-agent", self.user_agent)
        if data is not None:
            request.add_header("content-type", "application/json")
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    def me(self):
        return self.call("GET", "/me")

    def ws_ticket(self):
        return self.call("POST", "/auth/ws-ticket")["ticket"]

    def socket_url(self):
        return socket_url(self.base)

    def send(self, channel_id, content, *, message_id=None, reply_to_id=None, retries=5, sleep=time.sleep):
        """Posts a message, retrying only a genuinely uncertain failure.

        `message_id` is generated once, before any retry, and never
        regenerated between attempts: that is what makes a retried send safe
        rather than a second message. Never vary `content` across calls
        sharing one `message_id` - the server replays what it first stored,
        whatever a later attempt carries, and this method has no way to catch
        a caller doing that. `sleep` exists for tests; a real caller never
        needs it.
        """
        body = {"id": message_id or str(uuid.uuid4()), "content": content}
        if reply_to_id:
            body["reply_to_id"] = reply_to_id
        return call_with_retry(
            lambda: self.call("POST", f"/channels/{channel_id}/messages", body),
            retries=retries,
            sleep=sleep,
        )


def is_token_revoked(err):
    """Whether `err` is the terminal 401 that means the token was revoked.

    A 401 is never retried: the credential is gone, not the network. See
    "When a token is revoked" in docs/bots/building-bots.md.
    """
    return isinstance(err, urllib.error.HTTPError) and err.code == 401


def is_not_found(err):
    return isinstance(err, urllib.error.HTTPError) and err.code == 404
