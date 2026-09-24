#!/usr/bin/env python3
"""CANVAS_CHANNEL resolution: the new mode and the backward-compatible fallback; run directly: python3 test_canvas_channel.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as board  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402


def voice_channel(channel_id, name):
    return Channel({"id": channel_id, "name": name, "kind": "voice"})


def text_channel(channel_id, name):
    return Channel({"id": channel_id, "name": name, "kind": "text"})


def setup(*, channels, watched_channel_ids, canvas_channel_setting):
    client = FakeAsyncClient(me_id="bot-1")
    board.bot.client = client
    board.bot.space = Space(client)
    board.bot.space.channels = {c.id: c for c in channels}
    board.bot.channels = set(watched_channel_ids)
    board.CANVAS_CHANNEL = canvas_channel_setting


def expect_runtime_error(needle):
    try:
        board.resolve_canvas_channel()
    except RuntimeError as err:
        assert needle in str(err), str(err)
        return
    raise AssertionError("expected a RuntimeError")


def test_canvas_channel_resolves_by_id():
    setup(
        channels=[voice_channel("v1", "voice-room"), text_channel("t1", "general")],
        watched_channel_ids={"t1", "v1"}, canvas_channel_setting="v1",
    )
    assert board.resolve_canvas_channel().id == "v1"


def test_canvas_channel_resolves_by_name():
    setup(
        channels=[voice_channel("v1", "voice-room"), text_channel("t1", "general")],
        watched_channel_ids={"t1", "v1"}, canvas_channel_setting="voice-room",
    )
    assert board.resolve_canvas_channel().id == "v1"


def test_canvas_channel_refuses_a_text_channel():
    setup(channels=[text_channel("t1", "general")], watched_channel_ids={"t1"}, canvas_channel_setting="t1")
    expect_runtime_error("voice channel")


def test_canvas_channel_refuses_an_unknown_name():
    setup(channels=[], watched_channel_ids=set(), canvas_channel_setting="nope")
    expect_runtime_error("does not name a channel")


def test_canvas_channel_must_also_be_in_slimm_channels():
    setup(channels=[voice_channel("v1", "voice-room")], watched_channel_ids={"t1"}, canvas_channel_setting="v1")
    expect_runtime_error("SLIMM_CHANNELS")


def test_falls_back_to_the_single_configured_channel_when_unset():
    setup(channels=[voice_channel("v1", "voice-room")], watched_channel_ids={"v1"}, canvas_channel_setting=None)
    assert board.resolve_canvas_channel().id == "v1"


def test_fallback_refuses_when_more_than_one_channel_is_configured():
    setup(
        channels=[voice_channel("v1", "voice-room"), text_channel("t1", "general")],
        watched_channel_ids={"v1", "t1"}, canvas_channel_setting=None,
    )
    expect_runtime_error("CANVAS_CHANNEL")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
