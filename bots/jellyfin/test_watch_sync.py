#!/usr/bin/env python3
"""The party tells the call where it is (slim-m decision 0050); run directly: python3 test_watch_sync.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import session_registry  # noqa: E402
import stream_session  # noqa: E402
import watch_sync  # noqa: E402
from slimbots import ApiError  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from test_bot import _fake_start_pipeline, jellyfin, movie_for_watch, setup_with_voice  # noqa: E402

TICK = 0.02
watch_sync.MIN_GAP_SECONDS = 0.0


def writes(client, method=None):
    found = [(m, p.rsplit("/channels/", 1)[1], b) for m, p, b, _ in client.calls if "/watch-session" in p]
    return [w for w in found if method is None or w[0] == method]


def kinds(client):
    return [(m, p.split("/watch-session")[1] or "/") for m, p, _ in writes(client)]


async def settle(seconds=TICK * 6):
    await asyncio.sleep(seconds)


def scenario(body, *, stub=None):
    """Runs `body(client, session)` against a started party with a fast tick, then tears it down."""
    client = setup_with_voice()
    if stub is not None:
        stub(client)
    saved_tick, saved_pipeline = watch_sync.TICK_SECONDS, stream_session.WatchSession._start_pipeline
    watch_sync.TICK_SECONDS = TICK
    stream_session.WatchSession._start_pipeline = _fake_start_pipeline

    async def run():
        session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", FakeVoiceSession("c1"))
        await session.start(30.0)
        try:
            await body(client, session)
        finally:
            session.finished = True
            if session._monitor_task is not None:
                session._monitor_task.cancel()
            await session.sync.end()
            for task in list(jellyfin.bot._background_tasks):
                task.cancel()

    try:
        asyncio.run(run())
    finally:
        watch_sync.TICK_SECONDS, stream_session.WatchSession._start_pipeline = saved_tick, saved_pipeline
        session_registry.clear()
    return client


def test_starting_states_the_session_with_title_duration_and_position():
    async def body(client, session):
        await settle(TICK)
        method, path, put = writes(client)[0]
        assert (method, path) == ("PUT", "c1/watch-session")
        assert put["item_id"] == "m1" and put["title"] == "Inception" and put["playing"] is True
        assert put["duration_ms"] == 7_200_000 and 30_000 <= put["position_ms"] < 31_000
        assert put["controller_user_id"] == "u1" and "seeked" not in put

    scenario(body)


def test_ticks_keep_coming_while_paused_and_report_the_frozen_position():
    async def body(client, session):
        session.pause()
        await settle()
        ticks = writes(client, "POST")
        assert len(ticks) >= 2
        assert all(t[2]["playing"] is False and 30_000 <= t[2]["position_ms"] < 31_000 for t in ticks)

    scenario(body)


def test_pause_and_resume_are_each_one_put_without_seeked():
    async def body(client, session):
        await settle(TICK)
        session.pause()
        await settle(TICK * 2)
        session.resume()
        await settle(TICK * 2)
        puts = [w[2] for w in writes(client, "PUT")]
        assert [p["playing"] for p in puts] == [True, False, True]
        assert all("seeked" not in p for p in puts)

    scenario(body)


def test_a_seek_puts_the_new_position_with_seeked_true():
    async def body(client, session):
        await settle(TICK)
        await session.seek(600)
        await settle(TICK * 2)
        put = writes(client, "PUT")[-1][2]
        assert put["seeked"] is True and 600_000 <= put["position_ms"] < 601_000

    scenario(body)


def test_a_burst_of_controls_is_folded_into_few_writes():
    async def body(client, session):
        await settle(TICK)
        before = len(writes(client, "PUT"))
        for _ in range(6):
            session.pause()
            session.resume()
        await settle(0.5)
        assert len(writes(client, "PUT")) - before <= 2

    watch_sync.MIN_GAP_SECONDS = 0.2
    try:
        scenario(body)
    finally:
        watch_sync.MIN_GAP_SECONDS = 0.0


def test_a_new_title_is_a_put_with_the_new_item():
    async def body(client, session):
        await settle(TICK)
        session._report_progress = lambda *a, **k: asyncio.sleep(0)
        await session.play_item(movie_for_watch("m2", "Heat"))
        await settle(TICK * 2)
        put = writes(client, "PUT")[-1][2]
        assert put["item_id"] == "m2" and put["title"] == "Heat" and "seeked" not in put

    scenario(body)


def test_stopping_ends_the_session_and_the_ticks_stop():
    async def body(client, session):
        await settle(TICK * 3)
        session._report_progress = lambda *a, **k: asyncio.sleep(0)
        await session.stop(announce=False)
        assert kinds(client)[-1] == ("DELETE", "/")
        count = len(writes(client))
        await settle()
        assert len(writes(client)) == count

    scenario(body)


def test_a_conflict_stops_syncing_but_not_the_party():
    async def body(client, session):
        await settle(TICK * 6)
        assert kinds(client) == [("PUT", "/")]
        assert session.sync.gave_up and not session.finished

    def stub(client):
        client.respond("PUT", "/channels/c1/watch-session", ApiError(409, {"error": "another bot is playing here"}))

    client = scenario(body, stub=stub)
    assert ("DELETE", "/") not in kinds(client)


def test_a_put_refused_before_the_call_heartbeat_lands_is_retried():
    async def body(client, session):
        await settle(TICK * 8)
        assert [k for k in kinds(client) if k[0] == "PUT"] == [("PUT", "/"), ("PUT", "/")]
        assert ("POST", "/tick") in kinds(client)

    def stub(client):
        answers = [ApiError(403, {"error": "not on the call"})]

        def answer():
            if answers:
                raise answers.pop()

        client.respond("PUT", "/channels/c1/watch-session", answer)

    scenario(body, stub=stub)


def test_a_tick_the_server_no_longer_knows_is_answered_with_a_fresh_put():
    async def body(client, session):
        await settle(TICK * 8)
        assert [k for k in kinds(client) if k[0] == "PUT"] == [("PUT", "/"), ("PUT", "/")]

    def stub(client):
        answers = [ApiError(404, {"error": "not found"})]

        def answer():
            if answers:
                raise answers.pop()

        client.respond("POST", "/channels/c1/watch-session/tick", answer)

    scenario(body, stub=stub)


def test_a_controller_the_server_refuses_is_dropped_and_the_session_still_stated():
    async def body(client, session):
        await settle(TICK * 8)
        puts = [w[2] for w in writes(client, "PUT")]
        assert "controller_user_id" in puts[0] and "controller_user_id" not in puts[1]
        assert not session.sync.gave_up

    def stub(client):
        answers = [ApiError(400, {"error": "controller_user_id cannot view this channel"})]

        def answer():
            if answers:
                raise answers.pop()

        client.respond("PUT", "/channels/c1/watch-session", answer)

    scenario(body, stub=stub)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
