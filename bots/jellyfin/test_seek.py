#!/usr/bin/env python3
"""Seek, skip and quality changes against a fake Jellyfin that reuses a transcode by its output key; run directly: python3 test_seek.py."""

import asyncio
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import controls  # noqa: E402
import jellyfin_core  # noqa: E402
import playback_progress  # noqa: E402
import quality  # noqa: E402
import session_registry  # noqa: E402
import stream_session  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from slimbots.voice import VoiceError  # noqa: E402
from test_bot import jellyfin, movie_for_watch, setup_with_voice  # noqa: E402

TICKS = 10_000_000


class FakeProcess:
    def __init__(self):
        self.returncode = None

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


class FakeJellyfin:
    """Jellyfin names a progressive transcode by item, device and play session, never by start time, and serves an existing one as is."""

    def __init__(self):
        self.jobs = {}
        self.launches = []
        self.processes = []
        self.fail_launch_after = None

    def served_start_seconds(self, url):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        key = (url.split("?")[0], query.get("DeviceId"), query.get("PlaySessionId"))
        return self.jobs.setdefault(key, int(query["StartTimeTicks"]) / TICKS)

    async def create_subprocess_exec(self, *args, **_kwargs):
        if self.fail_launch_after is not None and len(self.launches) >= self.fail_launch_after:
            raise stream_session.StreamError("ffmpeg is not on PATH")
        url = args[args.index("-i") + 1]
        self.launches.append(url)
        process = FakeProcess()
        self.processes.append(process)
        return process


async def _idle(self, _fifo_path):
    await asyncio.Event().wait()


class Rig:
    def __init__(self, voice_session=None, item=None):
        setup_with_voice()
        self.jf = FakeJellyfin()
        self.originals = (
            stream_session.ffmpeg_binary, stream_session.asyncio.create_subprocess_exec,
            stream_session.WatchSession._pump_video, stream_session.WatchSession._pump_audio,
            playback_progress.report_position, playback_progress.resolve_user_id, jellyfin_core.next_episode,
        )
        stream_session.ffmpeg_binary = lambda: "ffmpeg"
        stream_session.asyncio.create_subprocess_exec = self.jf.create_subprocess_exec
        stream_session.WatchSession._pump_video = _idle
        stream_session.WatchSession._pump_audio = _idle
        playback_progress.report_position = lambda *a, **k: None
        playback_progress.resolve_user_id = lambda: "u1"
        jellyfin_core.next_episode = lambda *a, **k: None
        self.voice = voice_session or FakeVoiceSession("c1")
        self.session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", item or movie_for_watch(), "u1", self.voice)
        session_registry.add(self.session)

    def run(self, coro):
        return asyncio.run(coro)

    def served(self):
        return [self.jf.served_start_seconds(url) for url in self.jf.launches]

    def close(self):
        (stream_session.ffmpeg_binary, stream_session.asyncio.create_subprocess_exec,
         stream_session.WatchSession._pump_video, stream_session.WatchSession._pump_audio,
         playback_progress.report_position, playback_progress.resolve_user_id, jellyfin_core.next_episode) = self.originals
        session_registry.clear()

    async def start(self, at=0.0):
        await self.session._publish()
        await self.session._start_pipeline(at)

    async def press(self, action):
        async with self.session.control_lock:
            await controls.apply(self.session, action)


def with_rig(test):
    def wrapper():
        rig = Rig()
        try:
            test(rig)
        finally:
            rig.close()
    wrapper.__name__ = test.__name__
    return wrapper


def _both_streams_share_a_url(rig):
    video, audio = rig.jf.launches[-2:]
    assert video == audio


@with_rig
def test_plus_thirty_restarts_the_stream_at_the_new_position(rig):
    async def scenario():
        await rig.start()
        await rig.press("fwd")
    rig.run(scenario())
    assert [round(s) for s in rig.served()[::2]] == [0, 30]
    assert [round(s) for s in rig.served()[1::2]] == [0, 30]
    _both_streams_share_a_url(rig)
    assert round(rig.session.position_seconds) == 30


@with_rig
def test_quality_change_resumes_the_stream_at_the_current_position(rig):
    async def scenario():
        await rig.start(600.0)
        await rig.press("q:low")
    rig.run(scenario())
    assert [round(s) for s in rig.served()] == [600, 600, 600, 600]
    assert rig.session.quality is quality.PRESETS["low"]


@with_rig
def test_subtitle_change_resumes_the_stream_at_the_current_position(rig):
    rig.session.item["MediaStreams"] = [{"Type": "Subtitle", "Index": 3, "Language": "eng", "DisplayTitle": "English"}]

    async def scenario():
        await rig.start(900.0)
        await rig.press("subs")
    rig.run(scenario())
    assert [round(s) for s in rig.served()] == [900, 900, 900, 900]
    assert "SubtitleStreamIndex=3" in rig.jf.launches[-1]


@with_rig
def test_two_rapid_plus_thirty_presses_add_sixty(rig):
    async def scenario():
        await rig.start()
        await asyncio.gather(rig.press("fwd"), rig.press("fwd"))
    rig.run(scenario())
    assert [round(s) for s in rig.served()[::2]] == [0, 30, 60]
    assert round(rig.session.position_seconds) == 60


@with_rig
def test_minus_thirty_never_goes_below_zero(rig):
    async def scenario():
        await rig.start(10.0)
        await rig.press("back")
    rig.run(scenario())
    assert round(rig.served()[-1]) == 0


@with_rig
def test_seek_while_paused_stays_paused_at_the_new_position(rig):
    async def scenario():
        await rig.start(100.0)
        rig.session.pause()
        await rig.press("fwd")
    rig.run(scenario())
    assert rig.session.paused
    assert abs(rig.session.position_seconds - 130.0) < 0.1


@with_rig
def test_quality_change_while_paused_stays_paused_at_the_same_position(rig):
    async def scenario():
        await rig.start(100.0)
        rig.session.pause()
        await rig.press("q:high")
    rig.run(scenario())
    assert rig.session.paused and abs(rig.session.position_seconds - 100.0) < 0.1
    assert round(rig.served()[-1]) == 100


@with_rig
def test_seek_beyond_the_end_stops_cleanly(rig):
    async def scenario():
        await rig.start(7190.0)
        await rig.press("fwd")
    rig.run(scenario())
    assert rig.session.finished and rig.voice.left
    assert len(rig.jf.launches) == 2
    assert all(p.returncode == -9 for p in rig.jf.processes)


@with_rig
def test_a_failed_seek_keeps_the_old_stream_playing(rig):
    async def scenario():
        await rig.start(100.0)
        rig.jf.fail_launch_after = 2
        try:
            await rig.press("fwd")
        except stream_session.StreamError:
            return True
        return False
    assert rig.run(scenario())
    assert all(p.returncode is None for p in rig.jf.processes)
    assert not rig.session.finished and not rig.session.paused


@with_rig
def test_a_failed_quality_change_restores_the_old_quality_and_stream(rig):
    voice = rig.voice
    real_publish = voice.publish_screen_share
    calls = []

    async def flaky_publish(**kwargs):
        calls.append(kwargs["width"])
        if len(calls) == 2:
            raise VoiceError("this token cannot publish")
        return await real_publish(**kwargs)

    voice.publish_screen_share = flaky_publish

    async def scenario():
        await rig.start(100.0)
        original = rig.session.quality
        try:
            await rig.press("q:low")
        except VoiceError:
            pass
        return original
    original = rig.run(scenario())
    assert rig.session.quality is original
    assert calls == [original.width, 854, original.width]
    assert round(rig.served()[-1]) == 100 and rig.jf.processes[-1].returncode is None
    assert not rig.session.finished


def test_a_new_stream_never_shares_a_transcode_with_the_previous_one():
    first = jellyfin_core.build_stream_url("m1", start_seconds=0, play_session_id="a")
    second = jellyfin_core.build_stream_url("m1", start_seconds=30, play_session_id="b")
    fake = FakeJellyfin()
    assert fake.served_start_seconds(first) == 0 and fake.served_start_seconds(second) == 30


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
