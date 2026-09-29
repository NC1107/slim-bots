"""A real connect and publish against a local `livekit-server --dev` (LIVEKIT_URL); skipped when none is configured."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time

import pytest

from slimbots import Bot
from slimbots.voice import VoiceSession, load_rtc

URL = os.environ.get("LIVEKIT_URL")
API_KEY, API_SECRET = "devkey", "secret"

pytestmark = pytest.mark.skipif(not URL, reason="LIVEKIT_URL is not set; the voice-real-livekit CI job provides a server")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_token(room: str, identity: str) -> str:
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    grants = {"roomJoin": True, "room": room, "canPublish": True}
    claims = {"iss": API_KEY, "sub": identity, "nbf": int(time.time()) - 10, "exp": int(time.time()) + 300, "video": grants}
    body = b64(json.dumps(claims).encode())
    signature = b64(hmac.new(API_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest())
    return f"{header}.{body}.{signature}"


def test_a_session_connects_and_publishes_both_screen_share_tracks() -> None:
    rtc = load_rtc()

    async def run() -> int:
        room = rtc.Room()
        await room.connect(URL, mint_token("slimbots-ci", "bot"), options=rtc.RoomOptions(auto_subscribe=False))
        try:
            session = VoiceSession(Bot(prefix="!"), "c1", room, True, rtc)
            await session.publish_screen_share(
                width=320, height=180, video_max_bitrate=1_000_000, video_max_framerate=15.0, audio_max_bitrate=64_000,
            )
            return len(room.local_participant.track_publications)
        finally:
            await room.disconnect()

    assert asyncio.run(run()) == 2
