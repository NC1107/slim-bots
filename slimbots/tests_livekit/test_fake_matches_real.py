"""The fake `livekit.rtc` in tests/test_voice.py may only advertise what the real module has, and accept what the real one accepts."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from slimbots.voice import load_rtc
from test_voice import FakeRoom, fake_rtc_module

FAKE = fake_rtc_module(FakeRoom())
NAMESPACED = ("LocalVideoTrack", "LocalAudioTrack", "TrackSource", "DegradationPreference")


def public_names(obj: Any) -> list[str]:
    return [name for name in vars(obj) if not name.startswith("_")]


def accepted_keywords(real: Any) -> set[str] | None:
    """Field names of a protobuf message or parameter names of a callable; None when neither can be introspected."""
    descriptor = getattr(real, "DESCRIPTOR", None)
    if descriptor is not None:
        return set(descriptor.fields_by_name)
    try:
        return set(inspect.signature(real).parameters)
    except (TypeError, ValueError):
        return None


@pytest.mark.parametrize("name", public_names(FAKE))
def test_the_fake_only_exposes_names_the_real_module_has(name: str) -> None:
    assert hasattr(load_rtc(), name), f"the fake invents rtc.{name}, which real livekit does not expose"


@pytest.mark.parametrize("name", NAMESPACED)
def test_the_fakes_nested_names_exist_on_the_real_objects(name: str) -> None:
    real, fake = getattr(load_rtc(), name), getattr(FAKE, name)
    for attr in public_names(fake):
        assert hasattr(real, attr), f"the fake invents rtc.{name}.{attr}"


@pytest.mark.parametrize("name", ["VideoSource", "AudioSource", "VideoEncoding", "TrackPublishOptions", "RoomOptions"])
def test_the_fakes_constructors_take_only_keywords_the_real_ones_accept(name: str) -> None:
    real_keywords = accepted_keywords(getattr(load_rtc(), name))
    if real_keywords is None:
        pytest.skip(f"real rtc.{name} cannot be introspected")
    fake_keywords = set(inspect.signature(getattr(FAKE, name)).parameters)
    assert fake_keywords <= real_keywords, f"the fake's rtc.{name} takes {sorted(fake_keywords - real_keywords)} the real one does not"
