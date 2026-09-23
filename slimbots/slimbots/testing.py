"""A Client that never touches the network, for unit-testing a bot's command
handlers without a live deployment.

This is not a mock of slim-m's API - it does not validate a request the way
the real server would, has no permissions, and will happily let two
concurrent `!flip all` both look like they succeeded. Money-safety and
concurrency need a real sqlite connection and real threads racing it, the
way bot-casino's own `test_concurrency.py` does. What `FakeClient` covers is
the layer above that: given an incoming message dict, does this bot's
handler send the right thing, to the right channel, with the right
`reply_to_id`.

It is deliberately not imported by `slimbots/__init__.py` - a template that
never writes a test should never need to know this module exists.
"""

import re

from .client import Client


class FakeClient(Client):
    """Duck-types `Client` well enough to pass into a handler unmodified:
    `.call`, `.send`, `.me` and `.ws_ticket` all work, none of them touch a
    socket.

    Every call is recorded, in order, in `.calls` as `(method, path, body)`.
    A POST to a channel's `/messages` route - what `.send` always resolves
    to - is additionally recorded in `.sent`, since "what did this bot
    actually say" is what most handler tests want to assert on, and gets a
    plausible auto-generated `{id, seq}` response so a test does not have to
    stub the one route every bot template calls on every command.

    Anything else the bot under test calls needs a response queued with
    `respond(method, path, response)` first; an unstubbed call raises
    immediately rather than hanging or returning `None`, so a test fails
    loud, at the call it forgot, not at a confusing assertion three lines
    later. `path` is matched literally - including any query string - so
    stub the exact path the handler calls.
    """

    _MESSAGE_ROUTE = re.compile(r"^/channels/[^/]+/messages$")

    def __init__(self, me_id="bot-1", base="https://fake.invalid", token="slimbot_fake", user_agent="fake/1.0"):
        super().__init__(base, token, user_agent)
        self.calls = []
        self.sent = []
        self._responses = {}
        self._next_seq = 1
        self.respond("GET", "/me", {"id": me_id})

    def respond(self, method, path, response):
        """Queues `response` - or, if `response` is callable, the result of
        calling it with no arguments - for every future `method path` call.
        Callable is for a response that must change between calls, like a
        cursor that advances; a plain value is reused as many times as
        asked."""
        self._responses[(method, path)] = response

    def call(self, method, path, body=None, *, raw_body=None, headers=None):
        recorded_body = body if body is not None else raw_body
        self.calls.append((method, path, recorded_body))

        is_send = method == "POST" and self._MESSAGE_ROUTE.match(path) is not None
        seq = None
        if is_send:
            seq = self._next_seq
            self._next_seq += 1
            self.sent.append({"channel_id": path.split("/")[2], "seq": seq, **(body or {})})

        response = self._responses.get((method, path))
        if response is not None:
            return response() if callable(response) else response
        if is_send:
            return {"id": (body or {}).get("id"), "seq": seq}
        raise KeyError(f"FakeClient: no response queued for {method} {path} - call .respond() first")
