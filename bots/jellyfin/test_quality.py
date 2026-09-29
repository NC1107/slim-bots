#!/usr/bin/env python3
"""`!quality` command and `WatchSession.set_quality`, on the fakes from test_bot.py; run directly: python3 test_quality.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import quality  # noqa: E402
import stream_session  # noqa: E402
import watch_cog  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from test_bot import _fake_start_pipeline, jellyfin, message, movie_for_watch, process, setup_with_voice  # noqa: E402


def running_session(voice_session=None):
    session = stream_session.WatchSession(
        jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", voice_session or FakeVoiceSession("c1"),
    )
    session._video_source = session._audio_source = object()
    watch_cog._set_active_session(session)
    return session


def patch_pipeline():
    original = (stream_session.WatchSession._start_pipeline, stream_session.WatchSession._teardown_pipeline)

    async def no_teardown(self):
        return None

    stream_session.WatchSession._start_pipeline = _fake_start_pipeline
    stream_session.WatchSession._teardown_pipeline = no_teardown
    return original


def restore_pipeline(original):
    stream_session.WatchSession._start_pipeline, stream_session.WatchSession._teardown_pipeline = original
    watch_cog._set_active_session(None)


def test_quality_alone_reports_the_current_setting():
    client = setup_with_voice()
    running_session()
    try:
        process(client, message("!quality"))
        reply = client.sent[-1]["content"]
        assert "quality is default (" in reply and "low|medium|high" in reply
    finally:
        watch_cog._set_active_session(None)


def test_quality_needs_a_running_stream():
    client = setup_with_voice()
    process(client, message("!quality high"))
    assert client.sent[-1]["content"] == "nothing is playing."


def test_quality_rejects_an_unknown_preset():
    client = setup_with_voice()
    running_session()
    try:
        process(client, message("!quality ultra"))
        assert 'no quality called "ultra"' in client.sent[-1]["content"]
    finally:
        watch_cog._set_active_session(None)


def test_quality_refuses_a_non_starter_non_manager():
    client = setup_with_voice()
    session = running_session()
    session.started_by_id = "someone-else"
    try:
        process(client, message("!quality low"))
        assert "only the person who started this" in client.sent[-1]["content"]
        assert session.quality.name == "default"
    finally:
        watch_cog._set_active_session(None)


def test_quality_republishes_at_the_preset_size_and_keeps_the_position():
    client = setup_with_voice()
    voice_session = FakeVoiceSession("c1")
    session = running_session(voice_session)
    session._seek_base = 600.0
    session.paused = True
    original = patch_pipeline()
    try:
        process(client, message("!quality low"))
    finally:
        restore_pipeline(original)
    assert session.quality is quality.PRESETS["low"]
    assert voice_session.publish_count == 1
    assert (voice_session.published["width"], voice_session.published["height"]) == (854, 480)
    assert voice_session.published["video_max_bitrate"] == 1_500_000
    assert session.paused and abs(session.position_seconds - 600) < 1
    assert client.sent[-1]["content"] == "quality set to low (854x480, up to 1500 kbps), resumed at 10:00."


def test_a_1080p_preset_warns_about_the_cpu_cost():
    client = setup_with_voice()
    running_session()
    original = patch_pipeline()
    try:
        process(client, message("!quality high"))
    finally:
        restore_pipeline(original)
    assert quality.HEAVY_WARNING in client.sent[-1]["content"]


def test_a_failed_republish_is_reported_not_raised():
    client = setup_with_voice()
    running_session(FakeVoiceSession("c1", can_publish=False))
    original = patch_pipeline()
    try:
        process(client, message("!quality low"))
    finally:
        restore_pipeline(original)
    assert client.sent[-1]["content"].startswith("could not switch quality:")


def test_the_stream_url_and_np_embed_follow_the_preset():
    url = stream_session.jellyfin_core.build_stream_url("m1", max_width=854, video_bitrate=1_500_000)
    assert "MaxWidth=854" in url and "VideoBitrate=1500000" in url
    session = running_session()
    try:
        session.quality = quality.PRESETS["medium"]
        fields = {f["name"]: f["value"] for f in session.now_playing_embed().to_wire()["fields"]}
        assert fields["quality"] == "medium (1280x720, up to 4000 kbps)"
    finally:
        watch_cog._set_active_session(None)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
