"""bot.voice: joins a channel's LiveKit room and can publish a screen-share track pair; see docs/framework.md."""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bot import Bot

HEARTBEAT_INTERVAL_SECONDS = 15.0


class VoiceError(Exception):
    """Raised when a channel has no voice configured, a join fails, or a session cannot publish."""


def load_rtc() -> Any:
    """A plain `importlib` load rather than a static import, so pyright never demands `livekit` be installed.
    Public so a streaming bot can build its own `VideoFrame`/`AudioFrame` against the same module a session uses."""
    try:
        return importlib.import_module("livekit.rtc")
    except ImportError as err:
        raise VoiceError("bot.voice needs the `livekit` package installed - pip install livekit") from err


class VoiceSession:
    """One joined LiveKit room: heartbeats itself over REST, and can publish a screen-share track pair."""

    def __init__(self, bot: "Bot", channel_id: str, room: Any, can_publish: bool, rtc: Any) -> None:
        self._bot = bot
        self.channel_id = channel_id
        self.room = room
        self.can_publish = can_publish
        self.rtc = rtc
        self._heartbeat_task: asyncio.Task[Any] | None = None

    def start_heartbeat(self) -> None:
        """Runs the REST heartbeat as a `bot.background()` task; see `crates/slimm-server/src/voice/heartbeat.rs`."""
        if self._heartbeat_task is None:
            self._heartbeat_task = self._bot.background(
                self._heartbeat_loop(), name=f"voice-heartbeat-{self.channel_id}"
            )

    async def _heartbeat_loop(self) -> None:
        assert self._bot.client is not None, "a voice session needs an open Bot connection"
        while True:
            await self._bot.client.voice_heartbeat(self.channel_id)
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def publish_screen_share(
        self, *, width: int, height: int, sample_rate: int = 48000, num_channels: int = 2,
    ) -> tuple[Any, Any]:
        """Publishes a video+audio pair tagged SCREEN_SHARE/SCREEN_SHARE_AUDIO - what a person's own share also uses."""
        if not self.can_publish:
            raise VoiceError("this token cannot publish - the bot needs SPEAK in this channel")
        rtc = self.rtc
        video_source = rtc.VideoSource(width, height, is_screencast=True)
        video_track = rtc.LocalVideoTrack.create_video_track("screen", video_source)
        await self.room.local_participant.publish_track(
            video_track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE)
        )
        audio_source = rtc.AudioSource(sample_rate, num_channels)
        audio_track = rtc.LocalAudioTrack.create_audio_track("screen-audio", audio_source)
        await self.room.local_participant.publish_track(
            audio_track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO)
        )
        return video_source, audio_source

    async def leave(self) -> None:
        """Stops the heartbeat, disconnects from the room, and forgets the heartbeat server-side; safe to call twice."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        await self.room.disconnect()
        assert self._bot.client is not None, "a voice session needs an open Bot connection"
        try:
            await self._bot.client.forget_voice_heartbeat(self.channel_id)
        except Exception:
            pass


class Voice:
    """`bot.voice` - mints a token through slim-m and connects to a channel's LiveKit room; see docs/framework.md."""

    def __init__(self, bot: "Bot") -> None:
        self._bot = bot

    async def join(self, channel_id: str) -> VoiceSession:
        """Mints a join token via `POST .../voice/token`, connects over LiveKit, and starts the session's heartbeat."""
        assert self._bot.client is not None, "voice.join needs an open Bot connection"
        token = await self._bot.client.voice_token(channel_id)
        rtc = load_rtc()
        room = rtc.Room()
        try:
            await room.connect(token["url"], token["token"], options=rtc.RoomOptions(auto_subscribe=False))
        except Exception as err:
            raise VoiceError(f"could not join channel {channel_id}'s voice room: {err}") from err
        session = VoiceSession(self._bot, channel_id, room, bool(token.get("can_publish")), rtc)
        session.start_heartbeat()
        return session
