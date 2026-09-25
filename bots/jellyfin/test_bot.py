#!/usr/bin/env python3
"""Grouping/cursor tests, plus command-layer tests with jf_get monkeypatched; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JELLYFIN_URL", "https://fake-jellyfin.invalid")
os.environ.setdefault("JELLYFIN_API_KEY", "fake-key")

import bot as jellyfin  # noqa: E402
import stream_session  # noqa: E402
import watch_cog  # noqa: E402
from slimbots import Permissions, Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient, FakeVoice, FakeVoiceSession  # noqa: E402
from slimbots.voice import VoiceError  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]


def episode(series_id, series_name, season, index, created="2024-01-01T00:00:00Z", eid=None):
    return {
        "Id": eid or f"ep-{series_id}-{season}-{index}", "Type": "Episode", "Name": f"Episode {index}",
        "SeriesId": series_id, "SeriesName": series_name, "SeasonName": season, "IndexNumber": index,
        "DateCreated": created, "_library_id": None,
    }


def movie(item_id, name, created="2024-01-01T00:00:00Z"):
    return {"Id": item_id, "Type": "Movie", "Name": name, "ProductionYear": 2020, "DateCreated": created, "_library_id": None}


def test_a_single_new_episode_posts_as_one_episode_card():
    posts = jellyfin.jellyfin_core.build_posts([episode("s1", "Show", "Season 1", 3)])
    assert len(posts) == 1
    assert "Episode 3" not in posts[0]["title"] or "New episode" in posts[0]["title"]


def test_multiple_new_episodes_group_by_series():
    items = [episode("s1", "Show", "Season 1", i) for i in (1, 2, 3)]
    posts = jellyfin.jellyfin_core.build_posts(items)
    assert len(posts) == 1
    assert "3 new episodes" in posts[0]["title"]


def test_movies_over_the_batch_threshold_collapse():
    items = [movie(f"m{i}", f"Movie {i}") for i in range(jellyfin.jellyfin_core.JELLYFIN_BATCH_THRESHOLD + 2)]
    posts = jellyfin.jellyfin_core.build_posts(items)
    assert len(posts) == 1
    assert "added" in posts[0]["title"]


def test_movies_under_the_batch_threshold_post_individually():
    items = [movie("m1", "Movie One"), movie("m2", "Movie Two")]
    posts = jellyfin.jellyfin_core.build_posts(items)
    assert len(posts) == 2


def test_cursor_only_moves_forward():
    conn = sqlite3.connect(":memory:")
    jellyfin.jellyfin_core.init_db(conn)
    jellyfin.jellyfin_core.advance_cursor(conn, "2024-06-01T00:00:00.0000000Z")
    jellyfin.jellyfin_core.advance_cursor(conn, "2024-01-01T00:00:00.0000000Z")
    assert jellyfin.jellyfin_core.get_cursor(conn) == "2024-06-01T00:00:00.0000000Z"


def test_already_posted_items_are_filtered_before_grouping():
    conn = sqlite3.connect(":memory:")
    jellyfin.jellyfin_core.init_db(conn)
    jellyfin.jellyfin_core.advance_cursor(conn, "2000-01-01T00:00:00.0000000Z")
    jellyfin.jellyfin_core.mark_posted(conn, ["m1"])
    original = jellyfin.jellyfin_core.items_since
    jellyfin.jellyfin_core.items_since = lambda cursor: [movie("m1", "Old"), movie("m2", "New")]
    try:
        fresh = jellyfin.jellyfin_core.fetch_new_items(conn)
    finally:
        jellyfin.jellyfin_core.items_since = original
    assert [item["Id"] for item in fresh] == ["m2"]


def test_excluded_genre_is_dropped():
    jellyfin.jellyfin_core.JELLYFIN_EXCLUDE_GENRES.add("horror")
    try:
        item = {**movie("m1", "Scary"), "Genres": ["Horror"]}
        assert jellyfin.jellyfin_core.is_excluded(item)
        item2 = {**movie("m2", "Not Scary"), "Genres": ["Comedy"]}
        assert not jellyfin.jellyfin_core.is_excluded(item2)
    finally:
        jellyfin.jellyfin_core.JELLYFIN_EXCLUDE_GENRES.discard("horror")


def test_library_routes_fall_back_to_the_default_channel():
    jellyfin.jellyfin_core.JELLYFIN_LIBRARY_ROUTES["lib-1"] = "routed-channel"
    jellyfin.bot.channels = {"default-channel"}
    try:
        assert jellyfin.jellyfin_core.target_channel(jellyfin.bot, "lib-1") == "routed-channel"
        assert jellyfin.jellyfin_core.target_channel(jellyfin.bot, "lib-2") == "default-channel"
    finally:
        jellyfin.jellyfin_core.JELLYFIN_LIBRARY_ROUTES.pop("lib-1", None)


def message(content, msg_id="m1"):
    return {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content}


def setup():
    jellyfin.bot.store = Store(":memory:", migrate=jellyfin.jellyfin_core.init_db)
    asyncio.run(jellyfin.bot.store.open())
    jellyfin.bot.channels = {"c1"}
    jellyfin.jellyfin_core._command_cooldown._last.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    jellyfin.bot.client = client
    jellyfin.bot.space = Space(client)
    jellyfin.bot.authors = AuthorFilter(client, space=jellyfin.bot.space, ignore_bots=True)
    jellyfin.bot.me_id = "bot-1"
    asyncio.run(jellyfin.bot.space.refresh_members())
    return client


def process(client, *messages):
    async def run():
        for msg in messages:
            await jellyfin.bot.process_message(msg)

    asyncio.run(run())


def _patch_search_items(items):
    original = jellyfin.jellyfin_core.search_items
    jellyfin.jellyfin_core.search_items = lambda query, limit: items
    return original


def test_search_returns_matching_items():
    client = setup()
    original = _patch_search_items([movie("m1", "Inception")])
    try:
        process(client, message("!jellyfin search inception"))
    finally:
        jellyfin.jellyfin_core.search_items = original
    assert "Inception" in client.sent[-1]["content"]


def test_search_with_no_query_shows_help():
    client = setup()
    process(client, message("!jellyfin search"))
    assert "commands:" in client.sent[-1]["content"]


def test_search_is_cooldown_limited():
    client = setup()
    original = _patch_search_items([movie("m1", "Inception")])
    try:
        process(client, message("!jellyfin search inception", "m1"))
        process(client, message("!jellyfin search inception", "m2"))
    finally:
        jellyfin.jellyfin_core.search_items = original
    assert "try again" in client.sent[-1]["content"]


def test_recent_reports_a_summary():
    client = setup()
    original = jellyfin.jellyfin_core.items_since
    jellyfin.jellyfin_core.items_since = lambda cutoff: [movie("m1", "A"), movie("m2", "B")]
    try:
        process(client, message("!jellyfin recent 3"))
    finally:
        jellyfin.jellyfin_core.items_since = original
    assert "2 item(s)" in client.sent[-1]["content"]


def test_recent_rejects_an_out_of_range_day_count():
    client = setup()
    process(client, message("!jellyfin recent 999"))
    assert "at most" in client.sent[-1]["content"]


def test_unrecognised_subcommand_shows_help():
    client = setup()
    process(client, message("!jellyfin nonsense"))
    assert "commands:" in client.sent[-1]["content"]


def test_another_bot_is_ignored_by_default():
    client = setup()
    client.respond(
        "GET", "/members",
        MEMBERS + [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []}],
    )
    asyncio.run(jellyfin.bot.space.refresh_members())
    process(client, {"id": "m1", "author_id": "bot-2", "channel_id": "c1", "content": "!jellyfin help"})
    assert client.sent == []


def test_poll_loop_propagates_a_terminal_jellyfin_auth_error():
    async def boom():
        raise jellyfin.jellyfin_core.JellyfinAuthError("nope")

    original = jellyfin.poll_once
    jellyfin.poll_once = boom
    try:
        raised = False
        try:
            asyncio.run(jellyfin.poll_loop())
        except jellyfin.jellyfin_core.JellyfinAuthError:
            raised = True
        assert raised
    finally:
        jellyfin.poll_once = original


def test_on_connect_registers_poll_loop_as_a_supervised_background_task():
    setup()
    jellyfin._background_started = False
    original = jellyfin.jellyfin_core.bootstrap_cursor
    jellyfin.jellyfin_core.bootstrap_cursor = lambda conn: None

    async def run():
        await jellyfin.on_connect()
        assert len(jellyfin.bot._background_tasks) == 1
        task = next(iter(jellyfin.bot._background_tasks))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(run())
    finally:
        jellyfin.jellyfin_core.bootstrap_cursor = original


def test_upload_poster_returns_the_uploaded_attachments_id():
    client = setup()
    original = jellyfin.jellyfin_core.jf_get_bytes
    jellyfin.jellyfin_core.jf_get_bytes = lambda path: b"\x89PNGdata"
    client.respond("POST", "/attachments?filename=poster.jpg", {"id": "att-1", "content_type": "image/png"})
    try:
        attachment_id = asyncio.run(jellyfin.upload_poster("item-1"))
    finally:
        jellyfin.jellyfin_core.jf_get_bytes = original
    assert attachment_id == "att-1"


def test_upload_poster_returns_none_when_jellyfin_has_no_image():
    client = setup()
    original = jellyfin.jellyfin_core.jf_get_bytes
    jellyfin.jellyfin_core.jf_get_bytes = lambda path: None
    try:
        attachment_id = asyncio.run(jellyfin.upload_poster("item-1"))
    finally:
        jellyfin.jellyfin_core.jf_get_bytes = original
    assert attachment_id is None
    assert not any(method == "POST" and path.startswith("/attachments") for method, path, _, _ in client.calls)


def setup_with_voice(*, can_publish=True, member_channels=None, voice_channels=None, join_error=None):
    client = setup()
    if member_channels is None:
        member_channels = {"u1": "c1"}
    jellyfin.bot.voice = FakeVoice(can_publish=can_publish, member_channels=member_channels, join_error=join_error)
    for channel in voice_channels or []:
        jellyfin.bot.space.channels[channel.id] = channel
    watch_cog._set_active_session(None)
    return client


def setup_with_manager():
    client = setup_with_voice()
    client.respond(
        "GET", "/roles", [{"id": "role-mgr", "name": "Manager", "permissions": Permissions.MANAGE_CHANNELS, "is_everyone": False}],
    )
    client.respond(
        "GET", "/members",
        MEMBERS + [{"id": "u2", "username": "mgr", "display_name": "Mgr", "is_bot": False, "is_webhook": False, "role_ids": ["role-mgr"]}],
    )
    asyncio.run(jellyfin.bot.space.refresh_roles())
    asyncio.run(jellyfin.bot.space.refresh_members())
    return client


def movie_for_watch(item_id="m1", name="Inception", runtime_seconds=7200):
    return {"Id": item_id, "Name": name, "Type": "Movie", "RunTimeTicks": int(runtime_seconds * 10_000_000), "MediaStreams": []}


async def _fake_start(self):
    self._video_source = object()
    self._audio_source = object()


async def _fake_start_pipeline(self, start_seconds):
    self._seek_base = start_seconds
    self._segment_started_at = stream_session.time.monotonic()


def test_start_publishes_the_configured_bitrate_and_framerate_ceilings():
    setup_with_voice()
    voice_session = FakeVoiceSession("c1")
    session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", voice_session)
    original_start_pipeline = stream_session.WatchSession._start_pipeline
    stream_session.WatchSession._start_pipeline = _fake_start_pipeline

    async def run():
        await session.start()
        session._monitor_task.cancel()
        try:
            await session._monitor_task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(run())
    finally:
        stream_session.WatchSession._start_pipeline = original_start_pipeline
    assert voice_session.published["video_max_bitrate"] == jellyfin.jellyfin_core.JELLYFIN_STREAM_WEBRTC_MAX_BITRATE
    assert voice_session.published["video_max_framerate"] == float(jellyfin.jellyfin_core.JELLYFIN_STREAM_FPS)
    assert voice_session.published["audio_max_bitrate"] == jellyfin.jellyfin_core.JELLYFIN_STREAM_AUDIO_MAX_BITRATE


def test_parse_hms_and_format_hms_round_trip():
    assert stream_session.parse_hms("1:02:03") == 3723
    assert stream_session.parse_hms("2:03") == 123
    assert stream_session.parse_hms("45") == 45
    assert stream_session.format_hms(3723) == "1:02:03"
    assert stream_session.format_hms(123) == "2:03"


def test_parse_hms_rejects_garbage():
    try:
        stream_session.parse_hms("not-a-time")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_frame_byte_size_is_the_i420_layout():
    assert stream_session.frame_byte_size(1280, 720) == 1280 * 720 * 3 // 2


def test_build_stream_url_carries_seek_and_subtitle_params():
    jellyfin.jellyfin_core.JELLYFIN_URL = "https://fake-jellyfin.invalid"
    url = jellyfin.jellyfin_core.build_stream_url("item-1", start_seconds=90, subtitle_stream_index=2)
    assert "StartTimeTicks=900000000" in url
    assert "SubtitleStreamIndex=2" in url
    assert "SubtitleMethod=Encode" in url


def test_find_subtitle_stream_matches_language_or_display_title():
    item = {"MediaStreams": [
        {"Type": "Subtitle", "Index": 3, "Language": "eng", "DisplayTitle": "English"},
        {"Type": "Subtitle", "Index": 4, "Language": "fre", "DisplayTitle": "French"},
        {"Type": "Audio", "Index": 1},
    ]}
    assert jellyfin.jellyfin_core.find_subtitle_stream(item, "english")["Index"] == 3
    assert jellyfin.jellyfin_core.find_subtitle_stream(item, "fre")["Index"] == 4
    assert jellyfin.jellyfin_core.find_subtitle_stream(item, "spanish") is None


def test_watch_requires_the_invoker_to_be_in_any_voice_call():
    client = setup_with_voice(member_channels={})
    original_start = stream_session.WatchSession.start
    stream_session.WatchSession.start = _fake_start
    try:
        process(client, message("!watch inception"))
    finally:
        stream_session.WatchSession.start = original_start
    assert client.sent[-1]["content"] == "join a voice channel first, then run `!watch` again."
    assert watch_cog.active_session() is None


def test_watch_streams_into_the_invokers_own_voice_channel_not_the_text_channel():
    """The command is typed in the text channel c1; the invoker is actually in the voice channel v1 - the bug this fixes."""
    client = setup_with_voice(
        member_channels={"u1": "v1"}, voice_channels=[Channel({"id": "v1", "name": "voice-room", "kind": "voice"})],
    )
    original_search = jellyfin.jellyfin_core.search_items
    original_fetch = jellyfin.jellyfin_core.fetch_item_for_playback
    jellyfin.jellyfin_core.search_items = lambda query, limit: [movie_for_watch()]
    jellyfin.jellyfin_core.fetch_item_for_playback = lambda item_id: movie_for_watch()
    original_start = stream_session.WatchSession.start
    stream_session.WatchSession.start = _fake_start
    try:
        process(client, message("!watch inception"))
    finally:
        jellyfin.jellyfin_core.search_items = original_search
        jellyfin.jellyfin_core.fetch_item_for_playback = original_fetch
        stream_session.WatchSession.start = original_start
    assert client.sent[-1]["content"] == "streaming **Inception** into #voice-room (2:00:00)."
    session = watch_cog.active_session()
    assert session is not None and session.title == "Inception"
    assert session.text_channel_id == "c1"
    assert session.voice_channel_id == "v1"
    assert jellyfin.bot.voice.sessions[-1].channel_id == "v1"
    watch_cog._set_active_session(None)


def test_watch_refuses_when_the_bot_lacks_speak_in_the_invokers_channel():
    client = setup_with_voice(
        can_publish=False, member_channels={"u1": "v1"},
        voice_channels=[Channel({"id": "v1", "name": "voice-room", "kind": "voice"})],
    )
    original_search = jellyfin.jellyfin_core.search_items
    original_fetch = jellyfin.jellyfin_core.fetch_item_for_playback
    jellyfin.jellyfin_core.search_items = lambda query, limit: [movie_for_watch()]
    jellyfin.jellyfin_core.fetch_item_for_playback = lambda item_id: movie_for_watch()
    try:
        process(client, message("!watch inception"))
    finally:
        jellyfin.jellyfin_core.search_items = original_search
        jellyfin.jellyfin_core.fetch_item_for_playback = original_fetch
    assert "need SPEAK" in client.sent[-1]["content"]
    assert "#voice-room" in client.sent[-1]["content"]
    assert watch_cog.active_session() is None


def test_watch_names_the_missing_permission_when_the_bot_cannot_connect():
    client = setup_with_voice(
        member_channels={"u1": "v1"}, voice_channels=[Channel({"id": "v1", "name": "voice-room", "kind": "voice"})],
        join_error=VoiceError("needs VIEW_CHANNEL and CONNECT in that channel"),
    )
    original_search = jellyfin.jellyfin_core.search_items
    original_fetch = jellyfin.jellyfin_core.fetch_item_for_playback
    jellyfin.jellyfin_core.search_items = lambda query, limit: [movie_for_watch()]
    jellyfin.jellyfin_core.fetch_item_for_playback = lambda item_id: movie_for_watch()
    try:
        process(client, message("!watch inception"))
    finally:
        jellyfin.jellyfin_core.search_items = original_search
        jellyfin.jellyfin_core.fetch_item_for_playback = original_fetch
    assert "CONNECT" in client.sent[-1]["content"]
    assert "#voice-room" in client.sent[-1]["content"]
    assert watch_cog.active_session() is None


def test_watch_refuses_a_second_stream_while_one_is_active():
    client = setup_with_voice()
    watch_cog._set_active_session(stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", None))
    try:
        process(client, message("!watch inception"))
        assert "already watching" in client.sent[-1]["content"]
    finally:
        watch_cog._set_active_session(None)


def test_pause_then_resume_updates_state():
    client = setup_with_voice()
    session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", FakeVoiceSession("c1"))
    watch_cog._set_active_session(session)
    try:
        process(client, message("!pause"))
        assert session.paused
        process(client, message("!np"))
        assert client.sent[-1].get("embeds")
        process(client, message("!resume"))
        assert not session.paused
    finally:
        watch_cog._set_active_session(None)


def test_pause_refuses_a_non_starter_non_manager():
    client = setup_with_voice()
    session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "someone-else", FakeVoiceSession("c1"))
    watch_cog._set_active_session(session)
    try:
        process(client, message("!pause"))
        assert "only the person who started this" in client.sent[-1]["content"]
        assert not session.paused
    finally:
        watch_cog._set_active_session(None)


def test_stop_allows_a_channel_manager_to_stop_someone_elses_stream():
    client = setup_with_manager()
    session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", FakeVoiceSession("c1"))
    watch_cog._set_active_session(session)
    try:
        process(client, {"id": "m2", "author_id": "u2", "channel_id": "c1", "content": "!stop"})
        assert session.finished
    finally:
        watch_cog._set_active_session(None)


def test_np_reports_nothing_playing_when_idle():
    client = setup_with_voice()
    process(client, message("!np"))
    assert "nothing is playing" in client.sent[-1]["content"]


def test_seek_and_subs_update_the_session():
    client = setup_with_voice()
    session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(runtime_seconds=3600), "u1", FakeVoiceSession("c1"))
    session.item["MediaStreams"] = [{"Type": "Subtitle", "Index": 3, "Language": "eng", "DisplayTitle": "English"}]
    watch_cog._set_active_session(session)

    async def fake_seek(self, seconds):
        if self.duration_seconds:
            seconds = min(seconds, self.duration_seconds)
        self._seek_base = max(0.0, seconds)
        self._segment_started_at = stream_session.time.monotonic()

    original_seek = stream_session.WatchSession.seek
    stream_session.WatchSession.seek = fake_seek
    try:
        process(client, message("!seek 10:00"))
        assert abs(session.position_seconds - 600) < 1
        process(client, message("!subs english"))
        assert session.subtitle_label == "English"
        process(client, message("!subs off"))
        assert session.subtitle_label is None
    finally:
        stream_session.WatchSession.seek = original_seek
        watch_cog._set_active_session(None)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
