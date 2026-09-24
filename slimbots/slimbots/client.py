"""The websocket URL rule every transport shares: never a token in plaintext outside loopback."""

import sys
import urllib.parse

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def socket_url(base: str) -> str:
    """The WebSocket URL for `base`, refusing to carry a token in plaintext outside loopback."""
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
