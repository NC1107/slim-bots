#!/usr/bin/env python3
"""Resume offers and playback-progress reporting for `!watch`; run directly: python3 test_resume.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jellyfin_core  # noqa: E402
import playback_progress  # noqa: E402
import stream_session  # noqa: E402
import session_registry  # noqa: E402
import watch_cog  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from test_bot import jellyfin, message, movie_for_watch, setup_with_voice  # noqa: E402

TEN_MINUTES_TICKS = 600 * playback_progress.TICKS_PER_SECOND


def with_position(item, seconds):
    item["UserData"] = {"PlaybackPositionTicks": int(seconds * playback_progress.TICKS_PER_SECOND)}
    return item


def voice_client():
    return setup_with_voice(
        member_channels={"u1": "v1"}, voice_channels=[Channel({"id": "v1", "name": "voice-room", "kind": "voice"})],
    )


class Patched:
    """Swaps module attributes for the duration of a with-block."""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for module, name, value in self.pairs:
            self.saved.append((module, name, getattr(module, name)))
            setattr(module, name, value)

    def __exit__(self, *_exc):
        for module, name, value in self.saved:
            setattr(module, name, value)
        session_registry.clear()


def run_watch_with_reply(text, reply):
    """Runs `!watch <text>` and, while it waits on the resume prompt, delivers the invoker's `reply`."""
    started = []

    async def fake_start(self, start_seconds=0.0):
        started.append(start_seconds)

    async def flow():
        watch = asyncio.create_task(jellyfin.bot.process_message(message(text)))
        await asyncio.sleep(0.05)
        if reply is not None:
            await jellyfin.bot._dispatch_event("on_raw_message", message(reply, msg_id="m2"))
        await watch

    with Patched((stream_session.WatchSession, "start", fake_start)):
        asyncio.run(flow())
    return started


def test_saved_position_ignores_the_first_half_minute_and_the_last_minute():
    item = movie_for_watch(runtime_seconds=7200)
    assert playback_progress.saved_position_seconds(with_position(dict(item), 10), 7200) == 0.0
    assert playback_progress.saved_position_seconds(with_position(dict(item), 600), 7200) == 600.0
    assert playback_progress.saved_position_seconds(with_position(dict(item), 7180), 7200) == 0.0
    assert playback_progress.saved_position_seconds(dict(item), 7200) == 0.0


def test_watch_offers_resume_from_jellyfins_own_position_and_starts_there():
    client = voice_client()
    fetched = []

    def fetch(item_id, user_id=None):
        fetched.append(user_id)
        return with_position(movie_for_watch(), 600)

    with Patched(
        (jellyfin_core, "search_items", lambda query, limit: [movie_for_watch()]),
        (jellyfin_core, "fetch_item_for_playback", fetch),
    ):
        started = run_watch_with_reply("!watch inception", "resume")
    assert fetched == ["jf-user"]
    assert "is at 10:00" in client.sent[0]["content"]
    assert started == [600.0]
    assert client.sent[-1]["content"] == "**Inception** in #voice-room\nplaying - 2:00:00 - 720p - subtitles off"


def test_watch_start_over_ignores_the_saved_position():
    client = voice_client()
    with Patched(
        (jellyfin_core, "search_items", lambda query, limit: [movie_for_watch()]),
        (jellyfin_core, "fetch_item_for_playback", lambda item_id, user_id=None: with_position(movie_for_watch(), 600)),
    ):
        started = run_watch_with_reply("!watch inception", "start")
    assert started == [0.0]
    assert client.sent[-1]["content"] == "**Inception** in #voice-room\nplaying - 2:00:00 - 720p - subtitles off"


def test_watch_without_an_answer_starts_nothing():
    client = voice_client()
    with Patched(
        (jellyfin_core, "search_items", lambda query, limit: [movie_for_watch()]),
        (jellyfin_core, "fetch_item_for_playback", lambda item_id, user_id=None: with_position(movie_for_watch(), 600)),
        (watch_cog, "PICK_TIMEOUT_SECONDS", 0.05),
    ):
        started = run_watch_with_reply("!watch inception", None)
    assert started == []
    assert client.sent[-1]["content"] == "timed out - `!watch` again to retry."


def test_watch_with_no_title_offers_the_last_unfinished_item():
    client = voice_client()
    last = with_position(movie_for_watch(item_id="m9", name="Heat"), 900)
    with Patched(
        (playback_progress, "fetch_last_watched", lambda: last),
        (jellyfin_core, "fetch_item_for_playback", lambda item_id, user_id=None: last),
    ):
        started = run_watch_with_reply("!watch", "yes")
    assert "**Heat** is at 15:00" in client.sent[0]["content"]
    assert started == [900.0]


def test_watch_with_no_title_and_nothing_to_resume_says_so():
    client = voice_client()
    with Patched((playback_progress, "fetch_last_watched", lambda: None)):
        started = run_watch_with_reply("!watch", None)
    assert started == []
    assert client.sent[-1]["content"].startswith("nothing to resume")


def test_an_item_with_no_saved_position_starts_without_a_prompt():
    client = voice_client()
    with Patched(
        (jellyfin_core, "search_items", lambda query, limit: [movie_for_watch()]),
        (jellyfin_core, "fetch_item_for_playback", lambda item_id, user_id=None: movie_for_watch()),
    ):
        started = run_watch_with_reply("!watch inception", None)
    assert started == [0.0]
    assert all("reply `resume`" not in sent["content"] for sent in client.sent)


def test_stop_reports_the_position_and_finishing_marks_it_played():
    reports = []

    def record(item_id, seconds, *, finished=False):
        reports.append((item_id, round(seconds), finished))

    with Patched((playback_progress, "report_position", record)):
        setup_with_voice()
        stopped = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", FakeVoiceSession("c1"))
        stopped._seek_base = 300.0
        stopped.paused = True
        finished = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", FakeVoiceSession("c1"))

        async def flow():
            await stopped.stop(announce=False)
            await finished._handle_finished()

        asyncio.run(flow())
    assert reports == [("m1", 300, False), ("m1", 0, True)]


def test_report_position_posts_ticks_and_the_finished_flag():
    posted = []
    with Patched(
        (playback_progress, "jf_post_json", lambda path, params, body: posted.append((path, params, body))),
        (playback_progress, "JELLYFIN_USER_ID", "jf-user"),
    ):
        playback_progress.report_position("abc", 12.5)
        playback_progress.report_position("abc", 99, finished=True)
    assert posted[0][0] == "/UserItems/abc/UserData" and posted[0][1] == {"userId": "jf-user"}
    assert posted[0][2]["PlaybackPositionTicks"] == 125_000_000 and "Played" not in posted[0][2]
    assert posted[1][2]["PlaybackPositionTicks"] == 0 and posted[1][2]["Played"] is True


def test_the_first_enabled_user_is_used_when_none_is_configured():
    calls = []

    def users(path, params=None):
        calls.append(path)
        return [{"Id": "off", "Policy": {"IsDisabled": True}}, {"Id": "on", "Policy": {}}]

    with Patched(
        (jellyfin_core, "jf_get", users), (playback_progress, "JELLYFIN_USER_ID", ""), (playback_progress, "_resolved_user_id", None),
    ):
        assert playback_progress.resolve_user_id() == "on"
        assert playback_progress.resolve_user_id() == "on"
    assert calls == ["/Users"]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
