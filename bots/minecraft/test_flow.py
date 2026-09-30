#!/usr/bin/env python3
"""Batching, the rate limits and the roster rules, on a fake clock; run directly: python3 test_flow.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mc_core as core  # noqa: E402
from flow import Batcher, Bridge, MinuteCap  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(interval=3, max_lines=20, posts=10, queue=200, announce=core.EVENT_KINDS):
    clock = Clock()
    batcher = Batcher(interval, max_lines, posts, queue, clock=clock)
    return clock, batcher, Bridge(announce, batcher)


def chat(player="Steve", text="hi"):
    return core.Event("chat", player, text)


def test_lines_are_batched_into_one_post_after_the_interval():
    clock, batcher, bridge = make()
    for i in range(3):
        bridge.handle(chat(text=f"m{i}"))
    assert batcher.take() is None, "too soon after start"
    clock.now += 3
    post = batcher.take()
    assert post.count("\n") == 2 and "m0" in post and "m2" in post
    assert batcher.take() is None


def test_a_post_holds_at_most_max_lines_and_the_rest_waits():
    clock, batcher, bridge = make(max_lines=2)
    for i in range(5):
        bridge.handle(chat(text=f"m{i}"))
    clock.now += 3
    assert batcher.take().count("\n") == 1
    clock.now += 3
    assert batcher.take().count("\n") == 1


def test_posts_per_minute_is_capped_and_recovers():
    clock, batcher, bridge = make(interval=1, posts=2)
    sent = 0
    for _ in range(6):
        bridge.handle(chat())
        clock.now += 1
        sent += batcher.take() is not None
    assert sent == 2
    clock.now += 61
    bridge.handle(chat())
    assert batcher.take() is not None


def test_overflowing_the_queue_drops_the_oldest_and_says_so():
    clock, batcher, bridge = make(queue=3, max_lines=50)
    for i in range(10):
        bridge.handle(chat(text=f"m{i}"))
    clock.now += 3
    post = batcher.take()
    assert "m9" in post and "m0" not in post
    assert "7 earlier lines were dropped" in post


def test_one_post_never_exceeds_the_length_cap():
    clock, batcher, bridge = make(max_lines=50)
    for _ in range(30):
        bridge.handle(chat(text="x" * 390))
    clock.now += 3
    assert len(batcher.take()) <= 1900


def test_minute_cap_slides():
    clock = Clock()
    cap = MinuteCap(2, clock)
    assert cap.allow() and cap.allow() and not cap.allow()
    clock.now += 61
    assert cap.allow()


def test_our_own_relayed_line_is_never_queued_back():
    _clock, batcher, bridge = make()
    assert bridge.handle(chat("Steve", "[slim] Nick: hello")) is False
    assert bridge.handle(chat("Steve", "  [slim] Nick: hello")) is False
    assert bridge.handle(chat("Steve", "hello")) is True


def test_a_death_line_only_counts_for_a_player_known_to_be_online():
    _clock, _batcher, bridge = make()
    assert bridge.handle(core.Event("death", "Thread", "died of boredom")) is False
    bridge.handle(core.Event("join", "Steve", ""))
    assert bridge.handle(core.Event("death", "Steve", "was slain by Zombie")) is True
    bridge.handle(core.Event("leave", "Steve", ""))
    assert bridge.online == set()
    assert bridge.handle(core.Event("death", "Steve", "was slain by Zombie")) is False


def test_a_speaker_the_bot_missed_joining_is_learned_from_chat():
    _clock, _batcher, bridge = make()
    bridge.handle(chat("Alex"))
    assert bridge.online == {"Alex"}


def test_announce_setting_filters_kinds_but_still_tracks_the_roster():
    _clock, batcher, bridge = make(announce=["chat"])
    assert bridge.handle(core.Event("join", "Steve", "")) is False
    assert bridge.online == {"Steve"}
    assert bridge.handle(chat()) is True


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
