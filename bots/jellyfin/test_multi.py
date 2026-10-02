#!/usr/bin/env python3
"""One watch party per voice channel from the one bot; run directly: python3 test_multi.py."""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jellyfin_core  # noqa: E402
import session_registry  # noqa: E402
import stream_session  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from test_bot import MEMBERS, jellyfin, movie_for_watch, process, setup_with_voice  # noqa: E402
from test_resume import Patched  # noqa: E402

EXTRA_MEMBERS = [
    {"id": uid, "username": name, "display_name": name, "is_bot": False, "is_webhook": False, "role_ids": []}
    for uid, name in (("u2", "sam"), ("u3", "kim"))
]


def command(author_id, content):
    return {"id": f"m-{author_id}-{content}", "author_id": author_id, "channel_id": "c1", "content": content}


def two_calls():
    client = setup_with_voice(
        member_channels={"u1": "v1", "u2": "v2", "u3": "v3"},
        voice_channels=[Channel({"id": f"v{n}", "name": f"call-{n}", "kind": "voice"}) for n in (1, 2, 3)],
    )
    client.respond("GET", "/members", MEMBERS + EXTRA_MEMBERS)
    asyncio.run(jellyfin.bot.space.refresh_members())
    return client


def watching():
    async def fake_start(self, start_seconds=0.0):
        self._video_source = self._audio_source = object()

    return Patched(
        (jellyfin_core, "watch_search", lambda query, limit: [movie_for_watch()]),
        (jellyfin_core, "fetch_item_for_playback", lambda item_id, user_id=None: movie_for_watch()),
        (stream_session.WatchSession, "start", fake_start),
    )


def start_both(client):
    with watching():
        process(client, command("u1", "!watch inception"))
        process(client, command("u2", "!watch inception"))
        live = dict(session_registry.live_sessions())
    for session in live.values():
        session_registry.add(session)
    return live


def test_two_calls_each_get_their_own_stream_from_the_one_bot():
    client = two_calls()
    live = start_both(client)
    assert sorted(live) == ["v1", "v2"]
    assert live["v1"].started_by_id == "u1" and live["v2"].started_by_id == "u2"
    assert sorted(s.channel_id for s in jellyfin.bot.voice.sessions) == ["v1", "v2"]
    assert client.sent[-1]["content"] == "Inception - 2:00:00 - 720p"
    session_registry.clear()


def test_a_second_stream_in_the_same_call_is_refused():
    client = two_calls()
    with watching():
        process(client, command("u1", "!watch inception"))
        jellyfin.bot.voice.member_channels["u2"] = "v1"
        process(client, command("u2", "!watch inception"))
        assert list(session_registry.live_sessions()) == ["v1"]
    assert "already watching **Inception** in #call-1" in client.sent[-1]["content"]
    assert len(jellyfin.bot.voice.sessions) == 1
    session_registry.clear()


def test_stop_ends_only_the_invokers_own_call():
    client = two_calls()
    live = start_both(client)
    for session in live.values():
        session.voice_session = FakeVoiceSession(session.voice_channel_id)
    process(client, command("u1", "!stop"))
    assert live["v1"].finished and not live["v2"].finished
    assert list(session_registry.live_sessions()) == ["v2"]
    session_registry.clear()


def test_pause_in_one_call_leaves_the_other_playing():
    client = two_calls()
    live = start_both(client)
    process(client, command("u2", "!pause"))
    assert live["v2"].paused and not live["v1"].paused
    session_registry.clear()


def test_a_person_in_a_call_with_no_stream_is_told_where_streams_are():
    client = two_calls()
    start_both(client)
    process(client, command("u3", "!np"))
    assert client.sent[-1]["content"] == "nothing is playing in #call-3 (streaming in #call-1, #call-2)."
    session_registry.clear()


def test_a_person_in_no_call_needs_to_pick_one_when_several_are_playing():
    client = two_calls()
    start_both(client)
    jellyfin.bot.voice.member_channels.pop("u1")
    process(client, command("u1", "!np"))
    assert "join the voice channel you mean" in client.sent[-1]["content"]
    session_registry.clear()


def test_a_person_in_no_call_reaches_the_only_stream():
    client = two_calls()
    with watching():
        process(client, command("u2", "!watch inception"))
        session_registry.add(next(iter(session_registry.live_sessions().values())))
        jellyfin.bot.voice.member_channels.pop("u1")
        process(client, command("u1", "!np"))
    assert client.sent[-1].get("embeds")
    session_registry.clear()


def test_voice_activity_wakes_only_that_calls_monitor():
    client = two_calls()
    live = start_both(client)
    handler = jellyfin.bot._listeners["on_voice_activity"][0]
    asyncio.run(handler(SimpleNamespace(channel_id="v2")))
    assert live["v2"]._wake_monitor.is_set() and not live["v1"]._wake_monitor.is_set()
    session_registry.clear()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
