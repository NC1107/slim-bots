"""One voice call's music player: a queue, an ffmpeg decode, and a microphone-source LiveKit track; see README.md."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import shutil
from collections import deque

from slimbots import Embed
from slimbots.voice import VoiceError

import music_core

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_CHUNK_MS = 20
AUDIO_MAX_BITRATE = 128_000
CHUNK_SAMPLES = AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_SAMPLES * AUDIO_CHANNELS * 2


class PlayerError(Exception):
    """Raised when playback cannot start - a missing ffmpeg binary, most often."""


def ffmpeg_binary():
    path = shutil.which("ffmpeg")
    if path is None:
        raise PlayerError("ffmpeg is not on PATH - install it on the bot's host")
    return path


def build_ffmpeg_args(track):
    """Decodes to raw s16le on stdout; the protocol whitelist keeps a URL from reaching `file:` or a socket."""
    args = [ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "http,https,tcp,tls,crypto"]
    args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    if track.headers:
        args += ["-headers", track.headers]
    args += ["-i", track.url, "-vn", "-ac", str(AUDIO_CHANNELS), "-ar", str(AUDIO_SAMPLE_RATE), "-f", "s16le", "pipe:1"]
    return args


def audio_encoding(rtc, max_bitrate):
    """livekit re-exports `VideoEncoding` on `rtc` but not `AudioEncoding` (1.1.20); fall back to the proto it lives in."""
    cls = getattr(rtc, "AudioEncoding", None) or importlib.import_module("livekit.rtc._proto.room_pb2").AudioEncoding
    return cls(max_bitrate=max_bitrate)


async def publish_microphone(voice_session):
    """A speaking-participant track (not a screen-share tile), at a music-grade bitrate; returns its `AudioSource`."""
    if not voice_session.can_publish:
        raise VoiceError("this token cannot publish - the bot needs SPEAK in this channel")
    rtc = voice_session.rtc
    source = rtc.AudioSource(AUDIO_SAMPLE_RATE, AUDIO_CHANNELS)
    track = rtc.LocalAudioTrack.create_audio_track("music", source)
    options = rtc.TrackPublishOptions(
        source=rtc.TrackSource.SOURCE_MICROPHONE, audio_encoding=audio_encoding(rtc, AUDIO_MAX_BITRATE),
    )
    await voice_session.room.local_participant.publish_track(track, options)
    return source


class MusicSession:
    """The queue and playback for one voice channel; `on_end` lets the registry forget it once it has left."""

    def __init__(self, bot, voice_channel_id, text_channel_id, voice_session, on_end):
        self.bot = bot
        self.voice_channel_id = voice_channel_id
        self.text_channel_id = text_channel_id
        self.voice_session = voice_session
        self.on_end = on_end
        self.queue: deque[music_core.Track] = deque()
        self.current: music_core.Track | None = None
        self.paused = False
        self.ended = False
        self._chunks_sent = 0
        self._played_any = False
        self._interrupted = False
        self._source = None
        self._process = None
        self._runner_task = None
        self._monitor_task = None
        self._wake_monitor = asyncio.Event()

    @property
    def position_seconds(self):
        return self._chunks_sent * AUDIO_CHUNK_MS / 1000

    async def start(self, first_track):
        self._source = await publish_microphone(self.voice_session)
        self.queue.append(first_track)
        self._runner_task = self.bot.background(self._run(), name=f"music-runner-{self.voice_channel_id}")
        self._monitor_task = self.bot.background(self._monitor_loop(), name=f"music-monitor-{self.voice_channel_id}")

    def enqueue(self, track):
        """The 1-based queue position, or None when the queue is full."""
        if len(self.queue) >= music_core.MAX_QUEUE_LENGTH:
            return None
        self.queue.append(track)
        return len(self.queue)

    def wake_monitor(self):
        """Called from an `on_voice_activity` handler to check the roster now instead of on the next poll tick."""
        self._wake_monitor.set()

    def pause(self):
        if self.paused or self.current is None:
            return False
        self.paused = True
        return True

    def resume(self):
        if not self.paused:
            return False
        self.paused = False
        return True

    def skip(self):
        """Ends the current track; the runner then moves on to the next queued one. False when nothing is playing."""
        if self.current is None:
            return False
        self.paused = False
        self._interrupted = True
        if self._process is not None and self._process.returncode is None:
            self._process.kill()
        return True

    async def stop(self, *, reason="stopped"):
        if self.ended:
            return
        self.ended = True  # before the kill, so the runner it unblocks cannot start its own leave and cancel this stop
        self.queue.clear()
        self._interrupted = True
        await self._kill_process()
        await self._leave()
        with contextlib.suppress(Exception):
            await self.bot.client.send(self.text_channel_id, f"music stopped ({reason}).")

    async def _run(self):
        while self.queue and not self.ended:
            self.current = self.queue.popleft()
            if self._played_any:
                await self._say(f"now playing **{self.current.title}**.")
            self._played_any = True
            try:
                await self._play(self.current)
            except PlayerError as err:
                await self._say(f"could not play **{self.current.title}**: {err}")
            self.current = None
        if not self.ended:
            await self._say("queue finished, leaving the call.")
            await self._leave()

    async def _play(self, track):
        self._chunks_sent = 0
        self._interrupted = False
        self._process = await asyncio.create_subprocess_exec(
            *build_ffmpeg_args(track), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await self._pump(self._process.stdout)
        finally:
            await self._kill_process()

    async def _pump(self, stream):
        rtc = self.voice_session.rtc
        while True:
            while self.paused:
                await asyncio.sleep(0.1)
            try:
                chunk = await stream.readexactly(CHUNK_BYTES)
            except asyncio.IncompleteReadError as tail:
                if tail.partial and not self._interrupted:
                    await self._capture(rtc, tail.partial.ljust(CHUNK_BYTES, b"\x00"))
                return
            await self._capture(rtc, chunk)

    async def _capture(self, rtc, chunk):
        frame = rtc.AudioFrame(chunk, AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, CHUNK_SAMPLES)
        await self._source.capture_frame(frame)
        self._chunks_sent += 1

    async def _kill_process(self):
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.kill()
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                await process.wait()

    async def _leave(self):
        self.ended = True
        for task in (self._runner_task, self._monitor_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        self.on_end(self)
        await self.voice_session.leave()

    async def _say(self, text):
        with contextlib.suppress(Exception):
            await self.bot.client.send(self.text_channel_id, text)

    async def _monitor_loop(self):
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_monitor.wait(), timeout=music_core.ROSTER_POLL_SECONDS)
            self._wake_monitor.clear()
            if self.ended:
                return
            roster = await self.bot.client.voice_roster(self.voice_channel_id)
            others = [p for p in roster.get("participants", []) if p.get("user_id") != self.bot.me_id]
            if not others:
                await self.stop(reason="the call is empty")
                return

    def now_playing_embed(self):
        track = self.current
        embed = Embed(title=track.title if track else "nothing playing")
        if track is not None:
            embed.add_field("position", music_core.format_hms(self.position_seconds), inline=True)
            if track.duration_seconds:
                embed.add_field("duration", music_core.format_hms(track.duration_seconds), inline=True)
            embed.add_field("state", "paused" if self.paused else "playing", inline=True)
        embed.add_field("up next", str(len(self.queue)), inline=True)
        return embed
