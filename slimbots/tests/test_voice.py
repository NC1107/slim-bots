"""`bot.voice`: joining, publishing a screen-share pair, and leaving - against a fake `livekit.rtc`, never a real one."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from slimbots import Bot, VoiceError
from slimbots.testing import FakeAsyncClient
from slimbots import voice as voice_module


class FakeLocalParticipant:
    def __init__(self) -> None:
        self.published: list[tuple[Any, Any]] = []

    async def publish_track(self, track: Any, options: Any) -> Any:
        self.published.append((track, options))
        return SimpleNamespace(sid="pub-1")


class FakeRoom:
    def __init__(self) -> None:
        self.local_participant = FakeLocalParticipant()
        self.connected_to: tuple[str, str] | None = None
        self.disconnected = False

    async def connect(self, url: str, token: str, options: Any = None) -> None:
        self.connected_to = (url, token)

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeTrackSource:
    SOURCE_SCREENSHARE = "screenshare"
    SOURCE_SCREENSHARE_AUDIO = "screenshare_audio"


class FakeDegradationPreference:
    MAINTAIN_RESOLUTION = "maintain_resolution"


def fake_rtc_module(room: FakeRoom) -> SimpleNamespace:
    return SimpleNamespace(
        Room=lambda: room,
        RoomOptions=lambda auto_subscribe=True: SimpleNamespace(auto_subscribe=auto_subscribe),
        VideoSource=lambda width, height, is_screencast=False: SimpleNamespace(
            width=width, height=height, is_screencast=is_screencast,
        ),
        AudioSource=lambda sample_rate, num_channels: SimpleNamespace(
            sample_rate=sample_rate, num_channels=num_channels,
        ),
        LocalVideoTrack=SimpleNamespace(create_video_track=lambda name, source: SimpleNamespace(name=name, source=source)),
        LocalAudioTrack=SimpleNamespace(create_audio_track=lambda name, source: SimpleNamespace(name=name, source=source)),
        VideoEncoding=lambda max_bitrate=None, max_framerate=None: SimpleNamespace(
            max_bitrate=max_bitrate, max_framerate=max_framerate,
        ),
        # No AudioEncoding on purpose: real livekit (1.1.20) re-exports VideoEncoding but not AudioEncoding.
        DegradationPreference=FakeDegradationPreference,
        TrackPublishOptions=lambda source=None, video_encoding=None, audio_encoding=None, degradation_preference=None: SimpleNamespace(
            source=source, video_encoding=video_encoding, audio_encoding=audio_encoding,
            degradation_preference=degradation_preference,
        ),
        TrackSource=FakeTrackSource,
    )


def make_bot() -> tuple[Bot, FakeAsyncClient, FakeRoom]:
    bot = Bot(prefix="!")
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/voice/token", {
        "url": "wss://fake.invalid", "room": "channel-c1", "token": "tok", "expires_at": 0, "can_publish": True,
    })
    client.respond("POST", "/channels/c1/voice/heartbeat", None)
    client.respond("DELETE", "/channels/c1/voice/heartbeat", None)
    bot.client = client
    room = FakeRoom()
    return bot, client, room


def test_join_mints_a_token_and_connects_to_the_room(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, room = make_bot()
    monkeypatch.setattr(voice_module, "load_rtc", lambda: fake_rtc_module(room))

    async def run() -> None:
        session = await bot.voice.join("c1")
        assert room.connected_to == ("wss://fake.invalid", "tok")
        assert session.can_publish is True
        assert session._heartbeat_task is not None
        session._heartbeat_task.cancel()  # avoid an unawaited task at test end
        with pytest.raises(asyncio.CancelledError):
            await session._heartbeat_task

    asyncio.run(run())
    assert ("POST", "/channels/c1/voice/token", None, None) in client.calls


def test_publish_screen_share_tags_both_tracks_with_the_screen_share_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, room = make_bot()
    monkeypatch.setattr(voice_module, "load_rtc", lambda: fake_rtc_module(room))

    async def run() -> None:
        session = await bot.voice.join("c1")
        video_source, audio_source = await session.publish_screen_share(width=1280, height=720)
        assert video_source.width == 1280
        assert audio_source.sample_rate == 48000
        sources = [options.source for _track, options in room.local_participant.published]
        assert sources == [FakeTrackSource.SOURCE_SCREENSHARE, FakeTrackSource.SOURCE_SCREENSHARE_AUDIO]
        video_options, _audio_options = (options for _track, options in room.local_participant.published)
        assert video_options.video_encoding is None
        assert video_options.degradation_preference == FakeDegradationPreference.MAINTAIN_RESOLUTION
        session._heartbeat_task.cancel()

    asyncio.run(run())


def _stub_proto_audio_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real livekit resolves AudioEncoding from its proto module; CI has no livekit, so stand in for that import."""
    proto = SimpleNamespace(AudioEncoding=lambda max_bitrate=None: SimpleNamespace(max_bitrate=max_bitrate))
    monkeypatch.setattr(voice_module.importlib, "import_module", lambda name: proto)


def test_publish_screen_share_sets_explicit_ceilings_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, room = make_bot()
    monkeypatch.setattr(voice_module, "load_rtc", lambda: fake_rtc_module(room))
    _stub_proto_audio_encoding(monkeypatch)

    async def run() -> None:
        session = await bot.voice.join("c1")
        await session.publish_screen_share(
            width=1280, height=720, video_max_bitrate=8_000_000, video_max_framerate=30.0, audio_max_bitrate=128_000,
        )
        video_options, audio_options = (options for _track, options in room.local_participant.published)
        assert video_options.video_encoding.max_bitrate == 8_000_000
        assert video_options.video_encoding.max_framerate == 30.0
        assert audio_options.audio_encoding.max_bitrate == 128_000
        session._heartbeat_task.cancel()

    asyncio.run(run())


def test_publish_screen_share_refuses_a_listen_only_token(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, room = make_bot()
    client.respond("POST", "/channels/c1/voice/token", {
        "url": "wss://fake.invalid", "room": "channel-c1", "token": "tok", "expires_at": 0, "can_publish": False,
    })
    monkeypatch.setattr(voice_module, "load_rtc", lambda: fake_rtc_module(room))

    async def run() -> None:
        session = await bot.voice.join("c1")
        with pytest.raises(VoiceError):
            await session.publish_screen_share(width=1280, height=720)
        session._heartbeat_task.cancel()

    asyncio.run(run())


def test_leave_disconnects_and_forgets_the_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, room = make_bot()
    monkeypatch.setattr(voice_module, "load_rtc", lambda: fake_rtc_module(room))

    async def run() -> None:
        session = await bot.voice.join("c1")
        await session.leave()
        assert room.disconnected
        assert session._heartbeat_task is None

    asyncio.run(run())
    assert ("DELETE", "/channels/c1/voice/heartbeat", None, None) in client.calls


def test_load_rtc_raises_a_clear_voice_error_when_livekit_is_missing() -> None:
    original = importlib.import_module

    def fake_import(name: str) -> Any:
        if name == "livekit.rtc":
            raise ImportError("no module named livekit")
        return original(name)

    importlib.import_module = fake_import  # type: ignore[assignment]
    try:
        with pytest.raises(VoiceError):
            voice_module.load_rtc()
    finally:
        importlib.import_module = original  # type: ignore[assignment]


def test_audio_encoding_falls_back_to_the_proto_when_rtc_lacks_the_public_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real livekit 1.1.20: VideoEncoding is on rtc, AudioEncoding only in the proto. The old code did rtc.AudioEncoding directly and broke !watch.
    rtc = SimpleNamespace()
    _stub_proto_audio_encoding(monkeypatch)
    enc = voice_module._audio_encoding(rtc, 128_000)
    assert enc.max_bitrate == 128_000


def test_audio_encoding_prefers_the_public_name_when_present() -> None:
    rtc = SimpleNamespace(AudioEncoding=lambda max_bitrate=None: SimpleNamespace(max_bitrate=max_bitrate))
    enc = voice_module._audio_encoding(rtc, 96_000)
    assert enc.max_bitrate == 96_000
