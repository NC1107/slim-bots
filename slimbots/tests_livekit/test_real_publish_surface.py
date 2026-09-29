"""publish_screen_share against the REAL `livekit.rtc`, no server: a missing or renamed attribute fails here, not on a prod !watch."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from slimbots import Bot
from slimbots.voice import VoiceSession, _audio_encoding, load_rtc


class RecordingParticipant:
    def __init__(self) -> None:
        self.published: list[tuple[Any, Any]] = []

    async def publish_track(self, track: Any, options: Any) -> None:
        self.published.append((track, options))


def make_session(rtc: Any) -> tuple[VoiceSession, RecordingParticipant]:
    participant = RecordingParticipant()
    room = SimpleNamespace(local_participant=participant)
    return VoiceSession(Bot(prefix="!"), "c1", room, True, rtc), participant


def test_every_attribute_publish_screen_share_reads_exists_on_the_real_module() -> None:
    rtc = load_rtc()
    for name in ("Room", "RoomOptions", "VideoSource", "AudioSource", "LocalVideoTrack", "LocalAudioTrack",
                 "VideoEncoding", "TrackPublishOptions", "TrackSource", "DegradationPreference"):
        assert hasattr(rtc, name), f"livekit.rtc has no {name}"
    assert hasattr(rtc.LocalVideoTrack, "create_video_track")
    assert hasattr(rtc.LocalAudioTrack, "create_audio_track")
    assert hasattr(rtc.TrackSource, "SOURCE_SCREENSHARE")
    assert hasattr(rtc.TrackSource, "SOURCE_SCREENSHARE_AUDIO")
    assert hasattr(rtc.DegradationPreference, "MAINTAIN_RESOLUTION")


def test_audio_encoding_resolves_to_a_real_class_carrying_the_bitrate() -> None:
    assert _audio_encoding(load_rtc(), 96000).max_bitrate == 96000


def test_publish_screen_share_builds_and_publishes_both_tracks_with_every_ceiling_set() -> None:
    rtc = load_rtc()
    session, participant = make_session(rtc)

    async def run() -> tuple[Any, Any]:
        return await session.publish_screen_share(
            width=320, height=180, video_max_bitrate=1_500_000, video_max_framerate=30.0, audio_max_bitrate=96_000,
        )

    video_source, audio_source = asyncio.run(run())
    assert isinstance(video_source, rtc.VideoSource)
    assert isinstance(audio_source, rtc.AudioSource)
    (video_track, video_options), (audio_track, audio_options) = participant.published
    assert isinstance(video_track, rtc.LocalVideoTrack)
    assert isinstance(audio_track, rtc.LocalAudioTrack)
    assert video_options.source == rtc.TrackSource.SOURCE_SCREENSHARE
    assert video_options.video_encoding.max_bitrate == 1_500_000
    assert audio_options.source == rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO
    assert audio_options.audio_encoding.max_bitrate == 96_000
