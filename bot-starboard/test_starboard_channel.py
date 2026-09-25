#!/usr/bin/env python3
"""STARBOARD_CHANNEL resolution; run directly: python3 test_starboard_channel.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as starboard  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402


def setup(*, channels, channel_setting):
    client = FakeAsyncClient(me_id="bot-1")
    starboard.bot.client = client
    starboard.bot.space = Space(client)
    starboard.bot.space.channels = {c.id: c for c in channels}
    starboard.STARBOARD_CHANNEL = channel_setting


def expect_runtime_error(needle):
    try:
        starboard.resolve_starboard_channel()
    except RuntimeError as err:
        assert needle in str(err), str(err)
        return
    raise AssertionError("expected a RuntimeError")


def test_resolves_by_id():
    setup(channels=[Channel({"id": "sb", "name": "highlights"})], channel_setting="sb")
    assert starboard.resolve_starboard_channel().id == "sb"


def test_resolves_by_name():
    setup(channels=[Channel({"id": "sb", "name": "highlights"})], channel_setting="highlights")
    assert starboard.resolve_starboard_channel().id == "sb"


def test_refuses_when_unset():
    setup(channels=[Channel({"id": "sb", "name": "highlights"})], channel_setting=None)
    expect_runtime_error("STARBOARD_CHANNEL")


def test_refuses_an_unknown_channel():
    setup(channels=[], channel_setting="nope")
    expect_runtime_error("does not name a channel")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
