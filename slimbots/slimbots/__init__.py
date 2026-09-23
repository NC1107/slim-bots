"""Shared plumbing for slim-m bot templates.

This is not a client library for slim-m's API in the discord.js sense: there
is no typed model of a channel or a message, no cache, and no object for
every route. slim-m's bot surface is small - log in with a token, call REST,
hold one websocket, track a per-scope `seq` - and that surface is what this
package covers, nothing more.

See `docs/bots/building-bots.md` in the slim-m repo for the protocol itself.
`bot-ping` in this repo stays free of this package on purpose, so there is
still one file that shows the whole protocol with nothing hidden.
"""

from . import cursor
from .client import Client, is_not_found, is_token_revoked, socket_url
from .retry import call_with_retry
from .runner import run_forever
from .ws import Connection

__all__ = [
    "Client",
    "Connection",
    "call_with_retry",
    "cursor",
    "is_not_found",
    "is_token_revoked",
    "run_forever",
    "socket_url",
]
