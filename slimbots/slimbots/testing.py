"""`FakeAsyncClient`, for testing a `Bot` end to end with no network; see docs/framework.md."""

from __future__ import annotations

import re
import time
from typing import Any

from .http import AsyncClient
from .voice import VoiceError


class FakeAsyncClient(AsyncClient):
    """Duck-types `AsyncClient`; unstubbed calls raise loud instead of hanging - see docs/framework.md."""

    _MESSAGE_ROUTE = re.compile(r"^/channels/[^/]+/messages$")
    _EPHEMERAL_ROUTE = re.compile(r"^/channels/[^/]+/ephemeral-messages$")

    def __init__(
        self, me_id: str = "bot-1", base: str = "https://fake.invalid",
        token: str = "slimbot_fake", user_agent: str = "fake/1.0",
    ) -> None:
        super().__init__(base, token, user_agent)
        self.calls: list[tuple[str, str, Any, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.ephemerals: list[dict[str, Any]] = []
        self._responses: dict[tuple[str, str], Any] = {}
        self._next_seq = 1
        self.respond("GET", "/me", {"id": me_id})
        self.respond("GET", "/channels", [])
        self.respond("GET", "/members", [])
        self.respond("GET", "/roles", [])
        self.respond("PUT", "/bots/commands", None)

    def respond(self, method: str, path: str, response: Any) -> None:
        """Queues `response` (or, if callable, its return value) for every future `method path` call."""
        self._responses[(method, path)] = response

    async def call(  # type: ignore[override]
        self, method: str, path: str, body: Any = None, *, params: Any = None, headers: Any = None, **_kwargs: Any,
    ) -> Any:
        self.calls.append((method, path, body, params))
        seq = self._record_send(method, path, body)
        self._record_ephemeral(method, path, body)
        key = (method, path)
        if key in self._responses:
            return self._queued(self._responses[key])
        if seq is not None:
            return {"id": (body or {}).get("id"), "seq": seq}
        found, default = self._default_response(method, path, body)
        if found:
            return default
        raise KeyError(f"FakeAsyncClient: no response queued for {method} {path} - call .respond() first")

    def _record_send(self, method: str, path: str, body: Any) -> int | None:
        """Numbers and records a message send; None for any other call."""
        if method != "POST" or self._MESSAGE_ROUTE.match(path) is None:
            return None
        seq = self._next_seq
        self._next_seq += 1
        self.sent.append({"channel_id": path.split("/")[2], "seq": seq, **(body or {})})
        return seq

    def _record_ephemeral(self, method: str, path: str, body: Any) -> None:
        if method == "POST" and self._EPHEMERAL_ROUTE.match(path) is not None:
            self.ephemerals.append({"channel_id": path.split("/")[2], **(body or {})})

    @staticmethod
    def _queued(response: Any) -> Any:
        if isinstance(response, BaseException):
            raise response
        return response() if callable(response) else response

    def _default_response(self, method: str, path: str, body: Any) -> tuple[bool, Any]:
        """What the real server answers to a route no test queued: `(True, response)`, or `(False, None)` if unknown."""
        if method == "POST" and self._EPHEMERAL_ROUTE.match(path) is not None:
            return True, {"id": f"ephemeral-{len(self.ephemerals)}", "channel_id": path.split("/")[2], **(body or {})}
        if method in ("PUT", "DELETE") and "/roles/" in path:
            return True, None
        if path.startswith("/members/") and path.endswith("/timeout"):
            if method == "DELETE":
                return True, None
            if method == "PUT":
                until = int((time.time() + (body or {})["duration_seconds"]) * 1000)
                return True, {"user_id": path.split("/")[2], "until": until}
        if method == "DELETE" and path.startswith("/channels/") and "/messages/" in path:
            return True, None
        return False, None

    async def aclose(self) -> None:
        """Closes the real httpx client `AsyncClient.__init__` opened underneath, even though `call` never uses it."""
        await super().aclose()


class FakeVoiceSession:
    """A fake `VoiceSession`: records what a bot published and whether it left, no LiveKit or network involved."""

    def __init__(self, channel_id: str, *, can_publish: bool = True) -> None:
        self.channel_id = channel_id
        self.can_publish = can_publish
        self.rtc: Any = None
        self.heartbeat_started = False
        self.published: dict[str, Any] | None = None
        self.publish_count = 0
        self.unpublish_count = 0
        self.left = False

    def start_heartbeat(self) -> None:
        self.heartbeat_started = True

    async def publish_screen_share(
        self, *, width: int, height: int, sample_rate: int = 48000, num_channels: int = 2,
        video_max_bitrate: int | None = None, video_max_framerate: float | None = None,
        audio_max_bitrate: int | None = None,
    ) -> tuple[Any, Any]:
        if not self.can_publish:
            raise VoiceError("this token cannot publish - the bot needs SPEAK in this channel")
        self.publish_count += 1
        self.published = {
            "width": width, "height": height, "sample_rate": sample_rate, "num_channels": num_channels,
            "video_max_bitrate": video_max_bitrate, "video_max_framerate": video_max_framerate,
            "audio_max_bitrate": audio_max_bitrate,
        }
        return FakeVideoSource(), FakeAudioSource()

    async def unpublish_screen_share(self) -> None:
        if self.published is not None:
            self.unpublish_count += 1
            self.published = None

    async def leave(self) -> None:
        self.left = True


class FakeVideoSource:
    """Stands in for a real `VideoSource`: `capture_frame` (sync, like the real one) just counts frames."""

    def __init__(self) -> None:
        self.frame_count = 0

    def capture_frame(self, *_args: Any, **_kwargs: Any) -> None:
        self.frame_count += 1


class FakeAudioSource:
    """Stands in for a real `AudioSource`: `capture_frame` (async, like the real one) just counts frames."""

    def __init__(self) -> None:
        self.frame_count = 0

    async def capture_frame(self, *_args: Any, **_kwargs: Any) -> None:
        self.frame_count += 1


class FakeVoice:
    """A fake `bot.voice`: `join()` hands back a `FakeVoiceSession` instead of a real LiveKit room."""

    def __init__(
        self, *, can_publish: bool = True, member_channels: dict[str, str] | None = None,
        join_error: VoiceError | None = None,
    ) -> None:
        self.can_publish = can_publish
        self.sessions: list[FakeVoiceSession] = []
        self.member_channels = dict(member_channels or {})
        self.join_error = join_error

    async def join(self, channel_id: str) -> FakeVoiceSession:
        if self.join_error is not None:
            raise self.join_error
        session = FakeVoiceSession(channel_id, can_publish=self.can_publish)
        self.sessions.append(session)
        return session

    async def find_member(self, user_id: str) -> str | None:
        """The channel id `member_channels` says `user_id` is in, or None - set it directly in a test."""
        return self.member_channels.get(user_id)
