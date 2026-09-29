#!/usr/bin/env python3
"""The now-playing panel and its buttons; run directly: python3 test_panel.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import controls  # noqa: E402
import jellyfin_core  # noqa: E402
import panel  # noqa: E402
import quality  # noqa: E402
import session_registry  # noqa: E402
import stream_session  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from test_bot import MEMBERS, jellyfin, movie_for_watch, process, setup_with_voice  # noqa: E402
from test_resume import Patched  # noqa: E402

SUBTITLE = {"Type": "Subtitle", "Index": 3, "Language": "eng", "DisplayTitle": "English"}


def episode_for_watch(item_id="e1", index=1):
    return {
        "Id": item_id, "Name": f"Pilot {index}", "Type": "Episode", "SeriesId": "s1", "RunTimeTicks": 1_800 * 10_000_000,
        "MediaStreams": [SUBTITLE],
    }


def client_in_call(members_in_call=("u1",)):
    client = setup_with_voice(
        member_channels={uid: "v1" for uid in members_in_call},
        voice_channels=[Channel({"id": "v1", "name": "voice-room", "kind": "voice"})],
    )
    return client


def started_session(client, item=None):
    """A live party with its panel posted, the pipeline calls replaced by recorders."""
    calls = []
    session = stream_session.WatchSession(
        jellyfin.bot, "c1", "v1", item or movie_for_watch(), "u1", FakeVoiceSession("v1"),
    )

    async def fake_pipeline(start_seconds):
        calls.append(start_seconds)
        session._seek_base = start_seconds
        session._segment_started_at = stream_session.time.monotonic()

    async def fake_teardown():
        return None

    session._start_pipeline = fake_pipeline
    session._teardown_pipeline = fake_teardown
    session.panel = panel.Panel(jellyfin.bot, "c1", "#voice-room")
    asyncio.run(session._publish())
    asyncio.run(session.panel.post(session))
    session_registry.add(session)
    session.calls = calls
    return session


def press(client, session, custom_id, user_id="u1"):
    frame = {
        "type": "interaction.created", "interaction_id": f"i-{len(client.acks)}-{len(client.ephemerals)}", "channel_id": "c1",
        "message_id": session.panel.message_id, "custom_id": custom_id, "user_id": user_id,
        "user_display_name": user_id, "created_at": 1,
    }
    async def deliver():
        await jellyfin.bot._handle_frame(frame)
        await asyncio.gather(*list(jellyfin.bot._background_tasks))

    asyncio.run(deliver())


def labels(wire):
    return [b["label"] for row in wire for b in row["buttons"]]


def by_label(wire, label):
    return next(b for row in wire for b in row["buttons"] if b["label"] == label)


def test_the_panel_carries_every_control_for_a_movie():
    client = client_in_call()
    started_session(client)
    posted = client.sent[-1]
    assert labels(posted["components"]) == [
        "Pause", "-30s", "+30s", "Stop", "Low 480p", "Medium 720p", "High 1080p", "Subtitles on",
    ]
    assert by_label(posted["components"], "Medium 720p")["disabled"] is True
    session_registry.clear()


def test_the_panel_offers_next_episode_for_an_episode_only():
    client = client_in_call()
    started_session(client, episode_for_watch())
    assert "Next episode" in labels(client.sent[-1]["components"])
    session_registry.clear()


def test_pause_button_pauses_and_redraws_the_panel_as_play():
    client = client_in_call()
    session = started_session(client)
    press(client, session, "jf:toggle")
    assert session.paused
    assert "paused at" in client.edited[-1]["content"]
    assert "Play" in labels(client.component_edits[-1]["components"])
    assert client.acks
    session_registry.clear()


def test_skip_buttons_seek_thirty_seconds_and_never_below_zero():
    client = client_in_call()
    session = started_session(client)
    press(client, session, "jf:fwd")
    press(client, session, "jf:back")
    press(client, session, "jf:back")
    assert [round(c) for c in session.calls] == [30, 0, 0]
    session_registry.clear()


def test_quality_button_switches_the_preset():
    client = client_in_call()
    session = started_session(client)
    press(client, session, "jf:q:low")
    assert session.quality.name == "low"
    assert by_label(client.component_edits[-1]["components"], "Low 480p")["disabled"] is True
    assert session.voice_session.unpublish_count == 1
    session_registry.clear()


def test_subtitle_button_turns_a_track_on_then_off():
    client = client_in_call()
    item = movie_for_watch()
    item["MediaStreams"] = [SUBTITLE]
    session = started_session(client, item)
    press(client, session, "jf:subs")
    assert session.subtitle_stream_index == 3
    assert "Subtitles off" in labels(client.component_edits[-1]["components"])
    press(client, session, "jf:subs")
    assert session.subtitle_stream_index is None
    session_registry.clear()


def test_subtitle_button_without_tracks_answers_privately():
    client = client_in_call()
    session = started_session(client)
    press(client, session, "jf:subs")
    assert client.ephemerals[-1]["content"] == "this title has no subtitle tracks."
    assert session.subtitle_stream_index is None
    session_registry.clear()


def test_stop_button_ends_the_party_and_disables_every_button():
    client = client_in_call()
    session = started_session(client)
    press(client, session, "jf:stop")
    assert session.finished
    last = client.component_edits[-1]["components"]
    assert all(b.get("disabled") for row in last for b in row["buttons"])
    assert "ended" in client.edited[-1]["content"]
    session_registry.clear()


def test_next_episode_button_moves_the_party_to_the_next_episode():
    client = client_in_call()
    session = started_session(client, episode_for_watch())
    with Patched((jellyfin_core, "next_episode", lambda item, user_id=None: episode_for_watch("e2", 2))):
        press(client, session, "jf:next")
        assert session.title == "Pilot 2" and session.item_id == "e2"
    session_registry.clear()


def test_next_episode_on_a_finale_says_so_in_the_channel():
    client = client_in_call()
    session = started_session(client, episode_for_watch())
    with Patched((jellyfin_core, "next_episode", lambda item, user_id=None: None)):
        press(client, session, "jf:next")
    assert client.sent[-1]["content"] == "that was the last episode."
    assert session.item_id == "e1"
    session_registry.clear()


def test_a_member_outside_the_call_is_refused_privately_and_nothing_changes():
    client = client_in_call(("u1",))
    session = started_session(client)
    press(client, session, "jf:toggle", user_id="u9")
    assert not session.paused
    assert client.ephemerals[-1]["content"] == "join #voice-room to use these controls."
    assert not client.acks
    session_registry.clear()


def test_a_member_in_a_different_call_is_refused_too():
    client = client_in_call(("u1",))
    jellyfin.bot.voice.member_channels["u2"] = "v2"
    session = started_session(client)
    press(client, session, "jf:stop", user_id="u2")
    assert not session.finished
    assert "join #voice-room" in client.ephemerals[-1]["content"]
    session_registry.clear()


def test_pressing_an_old_panel_after_the_party_ended_answers_privately():
    client = client_in_call()
    session = started_session(client)
    asyncio.run(session.stop())
    press(client, session, "jf:toggle")
    assert "has ended" in client.ephemerals[-1]["content"]
    session_registry.clear()


def test_the_text_pause_command_redraws_the_panel():
    client = client_in_call()
    session = started_session(client)
    from test_bot import message
    process(client, message("!pause"))
    assert "Play" in labels(client.component_edits[-1]["components"])
    session_registry.clear()


def test_next_episode_lookup_skips_the_current_item():
    seen = {}

    def fake_get(path, params=None):
        seen["path"], seen["params"] = path, params
        return {"Items": [{"Id": "e1"}, {"Id": "e2"}]}

    with Patched((jellyfin_core, "jf_get", fake_get)):
        upcoming = jellyfin_core.next_episode(episode_for_watch(), "jf-user")
    assert upcoming["Id"] == "e2"
    assert seen["path"] == "/Shows/s1/Episodes" and seen["params"]["startItemId"] == "e1" and seen["params"]["userId"] == "jf-user"
    assert jellyfin_core.next_episode(movie_for_watch()) is None


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
