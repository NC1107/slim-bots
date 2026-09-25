"""Jellyfin server-side transcode -> ffmpeg decode -> LiveKit screen-share publish pipeline for `!watch`; see README.md."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
import time

from slimbots import Embed

import jellyfin_core

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_CHUNK_MS = 20
ROSTER_POLL_SECONDS = 20


class StreamError(Exception):
    """Raised when the ffmpeg pipeline cannot be started - a missing binary, most often."""


def ffmpeg_binary():
    path = shutil.which("ffmpeg")
    if path is None:
        raise StreamError("ffmpeg is not on PATH - install it on the bot's host")
    return path


def build_video_args(url, headers, fifo_path, *, width, height, fps):
    """Letterboxes Jellyfin's own aspect-preserving transcode into an exact WxH - the size `VideoSource` publishes."""
    filters = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
    return [
        ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-headers", headers, "-i", url,
        "-map", "0:v:0", "-an", "-vf", filters, "-pix_fmt", "yuv420p", "-f", "rawvideo", "-y", fifo_path,
    ]


def build_audio_args(url, headers, fifo_path):
    return [
        ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-headers", headers, "-i", url,
        "-map", "0:a:0", "-vn", "-ac", str(AUDIO_CHANNELS), "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "s16le", "-y", fifo_path,
    ]


def frame_byte_size(width, height):
    """I420: a full-resolution Y plane plus two quarter-resolution chroma planes."""
    return width * height + 2 * ((width + 1) // 2) * ((height + 1) // 2)


def audio_chunk_samples():
    return AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS // 1000


def audio_chunk_bytes():
    return audio_chunk_samples() * AUDIO_CHANNELS * 2


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def parse_hms(text):
    """Parses `h:mm:ss`, `mm:ss`, or a bare second count; raises ValueError on anything else."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a valid time: {text!r}")
    numbers = [int(p) for p in parts]
    while len(numbers) < 3:
        numbers.insert(0, 0)
    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


class WatchSession:
    """One `!watch` playback: owns the ffmpeg pipeline, the LiveKit publish, and its own position/pause state."""

    def __init__(self, bot, text_channel_id, voice_channel_id, item, started_by_id, voice_session):
        self.bot = bot
        self.text_channel_id = text_channel_id
        self.voice_channel_id = voice_channel_id
        self.item = item
        self.item_id = item["Id"]
        self.title = item.get("Name") or "Unknown title"
        self.duration_seconds = (item.get("RunTimeTicks") or 0) / 10_000_000
        self.started_by_id = started_by_id
        self.voice_session = voice_session
        self.audio_stream_index = None
        self.subtitle_stream_index = None
        self.subtitle_label = None
        self.paused = False
        self.finished = False
        self._seek_base = 0.0
        self._segment_started_at = time.monotonic()
        self._video_source = None
        self._audio_source = None
        self._video_process = None
        self._audio_process = None
        self._video_task = None
        self._audio_task = None
        self._monitor_task = None
        self._tmpdir = None
        self._wake_monitor = asyncio.Event()

    @property
    def position_seconds(self):
        if self.paused or self.finished:
            return self._seek_base
        return self._seek_base + (time.monotonic() - self._segment_started_at)

    def wake_monitor(self):
        """Called from an `on_voice_activity` handler to check the roster now instead of on the next poll tick."""
        self._wake_monitor.set()

    async def start(self):
        self._video_source, self._audio_source = await self.voice_session.publish_screen_share(
            width=jellyfin_core.JELLYFIN_STREAM_WIDTH, height=jellyfin_core.JELLYFIN_STREAM_HEIGHT,
            sample_rate=AUDIO_SAMPLE_RATE, num_channels=AUDIO_CHANNELS,
            video_max_bitrate=jellyfin_core.JELLYFIN_STREAM_WEBRTC_MAX_BITRATE,
            video_max_framerate=float(jellyfin_core.JELLYFIN_STREAM_FPS),
            audio_max_bitrate=jellyfin_core.JELLYFIN_STREAM_AUDIO_MAX_BITRATE,
        )
        await self._start_pipeline(0.0)
        self._monitor_task = self.bot.background(self._monitor_loop(), name=f"jellyfin-watch-monitor-{self.voice_channel_id}")

    async def _start_pipeline(self, start_seconds):
        self._tmpdir = tempfile.mkdtemp(prefix="slimm-jellyfin-")
        video_fifo = os.path.join(self._tmpdir, "video.raw")
        audio_fifo = os.path.join(self._tmpdir, "audio.raw")
        os.mkfifo(video_fifo)
        os.mkfifo(audio_fifo)
        url = jellyfin_core.build_stream_url(
            self.item_id, start_seconds=start_seconds, audio_stream_index=self.audio_stream_index,
            subtitle_stream_index=self.subtitle_stream_index,
        )
        headers = f"Authorization: {jellyfin_core.jellyfin_auth_header()}\r\n"
        video_args = build_video_args(
            url, headers, video_fifo, width=jellyfin_core.JELLYFIN_STREAM_WIDTH,
            height=jellyfin_core.JELLYFIN_STREAM_HEIGHT, fps=jellyfin_core.JELLYFIN_STREAM_FPS,
        )
        audio_args = build_audio_args(url, headers, audio_fifo)
        self._video_process = await asyncio.create_subprocess_exec(
            *video_args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        self._audio_process = await asyncio.create_subprocess_exec(
            *audio_args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        self._seek_base = start_seconds
        self._segment_started_at = time.monotonic()
        self._video_task = asyncio.create_task(self._pump_video(video_fifo), name="jellyfin-video-pump")
        self._audio_task = asyncio.create_task(self._pump_audio(audio_fifo), name="jellyfin-audio-pump")

    async def _pump_video(self, fifo_path):
        """Reads fixed-size I420 frames and paces them to `JELLYFIN_STREAM_FPS`; pausing just stops reading the fifo,
        so ffmpeg blocks on its own full pipe buffer instead of needing a separate pause signal."""
        rtc = self.voice_session.rtc
        width, height = jellyfin_core.JELLYFIN_STREAM_WIDTH, jellyfin_core.JELLYFIN_STREAM_HEIGHT
        frame_size = frame_byte_size(width, height)
        frame_interval = 1.0 / jellyfin_core.JELLYFIN_STREAM_FPS
        handle = await asyncio.to_thread(open, fifo_path, "rb")
        ended_naturally = False
        try:
            frame_index = 0
            start = time.monotonic()
            while True:
                if self.paused:
                    await asyncio.sleep(0.1)
                    start = time.monotonic() - frame_index * frame_interval
                    continue
                chunk = await asyncio.to_thread(handle.read, frame_size)
                if len(chunk) < frame_size:
                    ended_naturally = True
                    return
                frame = rtc.VideoFrame(width, height, rtc.VideoBufferType.I420, chunk)
                self._video_source.capture_frame(frame, timestamp_us=int(time.monotonic() * 1_000_000))
                frame_index += 1
                delay = (start + frame_index * frame_interval) - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
        finally:
            handle.close()
            if ended_naturally:
                self.bot.background(self._handle_finished(), name=f"jellyfin-finished-{self.voice_channel_id}")

    async def _pump_audio(self, fifo_path):
        chunk_bytes = audio_chunk_bytes()
        chunk_samples = audio_chunk_samples()
        rtc = self.voice_session.rtc
        handle = await asyncio.to_thread(open, fifo_path, "rb")
        try:
            while True:
                if self.paused:
                    await asyncio.sleep(0.1)
                    continue
                chunk = await asyncio.to_thread(handle.read, chunk_bytes)
                if len(chunk) < chunk_bytes:
                    return
                frame = rtc.AudioFrame(chunk, AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, chunk_samples)
                await self._audio_source.capture_frame(frame)
        finally:
            handle.close()

    async def _teardown_pipeline(self):
        for task in (self._video_task, self._audio_task):
            if task is not None:
                task.cancel()
        for task in (self._video_task, self._audio_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for process in (self._video_process, self._audio_process):
            if process is not None and process.returncode is None:
                process.kill()
                with contextlib.suppress(ProcessLookupError):
                    await process.wait()
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._video_task = self._audio_task = None
        self._video_process = self._audio_process = None
        self._tmpdir = None

    def pause(self):
        if self.paused or self.finished:
            return False
        self._seek_base = self.position_seconds
        self.paused = True
        return True

    def resume(self):
        if not self.paused:
            return False
        self.paused = False
        self._segment_started_at = time.monotonic()
        return True

    async def seek(self, seconds):
        if self.duration_seconds:
            seconds = min(seconds, self.duration_seconds)
        seconds = max(0.0, seconds)
        was_paused = self.paused
        await self._teardown_pipeline()
        await self._start_pipeline(seconds)
        self.paused = was_paused

    async def set_subtitle(self, stream_index, label):
        position = self.position_seconds
        self.subtitle_stream_index = stream_index
        self.subtitle_label = label
        await self.seek(position)

    async def _handle_finished(self):
        self.finished = True
        text_channel_id, title = self.text_channel_id, self.title
        await self.stop(reason="finished", announce=False)
        with contextlib.suppress(Exception):
            await self.bot.client.send(text_channel_id, f"finished playing **{title}**.")

    async def stop(self, *, reason="stopped", announce=True):
        self.finished = True
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            self._monitor_task = None
        await self._teardown_pipeline()
        await self.voice_session.leave()
        if announce:
            with contextlib.suppress(Exception):
                await self.bot.client.send(self.text_channel_id, f"stopped **{self.title}** ({reason}).")

    async def _monitor_loop(self):
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_monitor.wait(), timeout=ROSTER_POLL_SECONDS)
            self._wake_monitor.clear()
            if self.finished:
                return
            roster = await self.bot.client.voice_roster(self.voice_channel_id)
            others = [p for p in roster.get("participants", []) if p.get("user_id") != self.bot.me_id]
            if not others:
                await self.stop(reason="the call is empty")
                return

    def now_playing_embed(self):
        embed = Embed(title=self.title)
        embed.add_field("position", format_hms(self.position_seconds), inline=True)
        if self.duration_seconds:
            embed.add_field("duration", format_hms(self.duration_seconds), inline=True)
        embed.add_field("state", "paused" if self.paused else "playing", inline=True)
        if self.subtitle_label:
            embed.add_field("subtitles", self.subtitle_label, inline=True)
        return embed
