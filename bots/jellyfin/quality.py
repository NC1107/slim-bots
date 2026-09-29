"""Named stream-quality presets for `!quality`; the resolution/bitrate pairs are what README.md's cost table measured."""

from __future__ import annotations

from dataclasses import dataclass

import jellyfin_core

HEAVY_WIDTH = 1920


@dataclass(frozen=True)
class Quality:
    name: str
    width: int
    height: int
    video_bitrate: int
    webrtc_bitrate: int | None = None

    @property
    def publish_bitrate(self):
        return self.webrtc_bitrate or self.video_bitrate

    def describe(self):
        return f"{self.name} ({self.width}x{self.height}, up to {self.video_bitrate // 1000} kbps)"

    @property
    def is_heavy(self):
        return self.width >= HEAVY_WIDTH


PRESETS = {
    "low": Quality("low", 854, 480, 1_500_000),
    "medium": Quality("medium", 1280, 720, 4_000_000),
    "high": Quality("high", 1920, 1080, 8_000_000),
}

HEAVY_WARNING = (
    "1080p is much heavier on the bot's host: in the README's measurement the software encode went "
    "from about 20% to about 87% of a core."
)


def configured_default():
    """What the operator's `JELLYFIN_STREAM_*` env settings ask for, so a stream that never sees `!quality` is unchanged."""
    return Quality(
        "default", jellyfin_core.JELLYFIN_STREAM_WIDTH, jellyfin_core.JELLYFIN_STREAM_HEIGHT,
        jellyfin_core.JELLYFIN_STREAM_MAX_BITRATE, jellyfin_core.JELLYFIN_STREAM_WEBRTC_MAX_BITRATE,
    )


def find_preset(name):
    return PRESETS.get((name or "").strip().lower())


def preset_names():
    return ", ".join(f"`{name}`" for name in PRESETS)
