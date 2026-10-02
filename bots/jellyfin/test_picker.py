#!/usr/bin/env python3
"""Button search, season and episode navigation, and the between-episodes offer; run directly: python3 test_picker.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jellyfin_core  # noqa: E402
import picker  # noqa: E402
import playback_progress  # noqa: E402
import session_registry  # noqa: E402
import stream_session  # noqa: E402
from test_bot import jellyfin, message, movie_for_watch, process  # noqa: E402
from test_panel import client_in_call, episode_for_watch, labels, started_session  # noqa: E402
from test_resume import Patched, press_button, settle  # noqa: E402


def series(name="Show", item_id="s1"):
    return {"Id": item_id, "Type": "Series", "Name": name, "ProductionYear": 2019}


def matches(count):
    return [{**movie_for_watch(f"m{i}", f"Movie {i}"), "ProductionYear": 2000 + i} for i in range(count)]


def episodes(count, season="Season 1"):
    return [
        {**episode_for_watch(f"e{i}", i), "Name": f"Ep {i}", "ParentIndexNumber": 1, "IndexNumber": i, "SeasonName": season}
        for i in range(1, count + 1)
    ]


async def _no_pipeline(self, start_seconds=0.0):
    return None


def searching(results, **more):
    pairs = [
        (stream_session.WatchSession, "start", _no_pipeline),
        (jellyfin_core, "watch_search", lambda query, limit: results),
        (jellyfin_core, "fetch_item_for_playback", lambda item_id, user_id=None: movie_for_watch(item_id, "Chosen")),
        (jellyfin_core, "series_seasons", lambda series_id, user_id=None: more.get("seasons", [])),
        (jellyfin_core, "season_episodes", lambda series_id, season_id, user_id=None: more.get("episodes", [])),
    ]
    return Patched(*pairs)


def chooser(client):
    return next(m for m in reversed(client.sent) if m.get("components"))


def click(custom_id, user_id="u1"):
    """Presses a button on the newest chooser and waits for the work to finish."""
    client = jellyfin.bot.client
    frame = press_button(custom_id, chooser(client)["id"], user_id)

    async def go():
        await jellyfin.bot._handle_frame(frame)
        await settle()

    asyncio.run(go())


def start_watch(client, text="!watch show"):
    process(client, message(text))


def test_several_matches_show_at_most_five_buttons_not_a_numbered_list():
    client = client_in_call()
    with searching(matches(8)):
        start_watch(client)
    sent = chooser(client)
    assert len(labels(sent["components"])) == 5 + 1
    assert labels(sent["components"])[0] == "Movie 0 (2000)"
    assert "reply with a number" not in sent["content"]


def test_choosing_a_movie_starts_the_party_and_closes_the_chooser():
    client = client_in_call()
    started = []

    async def fake_start(self, start_seconds=0.0):
        started.append(start_seconds)

    with searching(matches(3)), Patched((stream_session.WatchSession, "start", fake_start)):
        start_watch(client)
        click("jfp:sel:1")
        assert session_registry.session_for_channel("v1").title == "Chosen"
    assert started == [0.0]
    assert client.edited[0]["content"] == "starting **Movie 1**..."
    assert client.component_edits[0]["components"] == []


def test_a_series_leads_to_seasons_then_episodes_then_plays():
    client = client_in_call()
    seasons = [{"Id": "se1", "Name": "Season 1"}, {"Id": "se2", "Name": "Season 2"}]
    started = []

    async def fake_start(self, start_seconds=0.0):
        started.append(start_seconds)

    with searching([series(), series("Show Two", "s2")], seasons=seasons, episodes=episodes(3)), \
            Patched((stream_session.WatchSession, "start", fake_start)):
        start_watch(client)
        click("jfp:sel:0")
        assert labels(client.component_edits[-1]["components"])[:2] == ["Season 1", "Season 2"]
        click("jfp:sel:1")
        assert labels(client.component_edits[-1]["components"])[:3] == ["S1E1 Ep 1", "S1E2 Ep 2", "S1E3 Ep 3"]
        click("jfp:sel:2")
        assert session_registry.session_for_channel("v1") is not None
    assert started == [0.0]


def test_a_one_season_series_skips_the_season_step():
    client = client_in_call()
    with searching([series(), series("Other", "s2")], seasons=[{"Id": "se1", "Name": "Season 1"}], episodes=episodes(2)):
        start_watch(client)
        click("jfp:sel:0")
    assert labels(client.component_edits[-1]["components"])[:2] == ["S1E1 Ep 1", "S1E2 Ep 2"]


def test_episodes_page_ten_at_a_time_and_back_returns_to_the_seasons():
    client = client_in_call()
    seasons = [{"Id": "se1", "Name": "Season 1"}, {"Id": "se2", "Name": "Season 2"}]
    with searching([series(), series("Other", "s2")], seasons=seasons, episodes=episodes(23)):
        start_watch(client)
        click("jfp:sel:0")
        click("jfp:sel:0")
        first = labels(client.component_edits[-1]["components"])
        assert first[0] == "S1E1 Ep 1" and first[9] == "S1E10 Ep 10" and "Next page" in first and "Previous" not in first
        assert "page 1 of 3" in client.edited[-1]["content"]
        click("jfp:next")
        click("jfp:next")
        last = labels(client.component_edits[-1]["components"])
        assert last[0] == "S1E21 Ep 21" and "Next page" not in last and "Previous" in last
        click("jfp:sel:22")
        assert session_registry.session_for_channel("v1") is not None


def test_back_from_the_episodes_returns_to_the_seasons_and_then_the_matches():
    client = client_in_call()
    seasons = [{"Id": "se1", "Name": "Season 1"}, {"Id": "se2", "Name": "Season 2"}]
    with searching([series(), series("Other", "s2")], seasons=seasons, episodes=episodes(2)):
        start_watch(client)
        click("jfp:sel:0")
        click("jfp:sel:0")
        click("jfp:back")
        assert labels(client.component_edits[-1]["components"])[:2] == ["Season 1", "Season 2"]
        click("jfp:back")
        assert labels(client.component_edits[-1]["components"])[:2] == ["Show (2019) - series", "Other (2019) - series"]


def test_someone_else_pressing_the_choice_is_refused_privately():
    client = client_in_call()
    with searching(matches(3)):
        start_watch(client)
        click("jfp:sel:0", user_id="u9")
    assert client.ephemerals[-1]["content"].startswith("only ")
    assert not client.acks and session_registry.live_sessions() == {}


def test_cancel_clears_the_buttons():
    client = client_in_call()
    with searching(matches(3)):
        start_watch(client)
        click("jfp:cancel")
    assert client.edited[-1]["content"] == "cancelled."
    assert client.component_edits[-1]["components"] == []
    assert not picker._picks


def test_a_press_on_an_expired_choice_answers_privately():
    client = client_in_call()
    with searching(matches(3)):
        start_watch(client)
        next(iter(picker._picks.values())).created_at -= picker.EXPIRES_SECONDS + 1
        click("jfp:sel:0")
    assert client.edited[-1]["content"] == "timed out - `!watch` again to retry."
    assert "expired" in client.ephemerals[-1]["content"]


def test_a_single_playable_match_still_plays_without_a_chooser():
    client = client_in_call()

    async def fake_start(self, start_seconds=0.0):
        return None

    with searching(matches(1)), Patched((stream_session.WatchSession, "start", fake_start)):
        start_watch(client)
        assert session_registry.session_for_channel("v1") is not None
    assert not any(m.get("components") and "matches" in m["content"] for m in client.sent)


def finish(session):
    asyncio.run(session._handle_finished())


def test_a_finished_episode_waits_for_next_episode_and_keeps_the_call():
    client = client_in_call()
    session = started_session(client, episode_for_watch())
    with Patched((jellyfin_core, "next_episode", lambda item, user_id=None: episode_for_watch("e2", 2)),
                 (jellyfin_core, "JELLYFIN_NEXT_WAIT_SECONDS", 999)):
        async def go():
            await session._handle_finished()
            session._cancel_next_timer()

        asyncio.run(go())
    assert not session.finished and session.waiting_next["Id"] == "e2"
    assert "next up: **Pilot 2**" in client.sent[-1]["content"]
    assert "next up: Pilot 2" in client.edited[-1]["content"]
    enabled = [b["label"] for row in client.component_edits[-1]["components"] for b in row["buttons"] if not b.get("disabled")]
    assert set(enabled) == {"Stop", "Next episode"}
    session_registry.clear()


def test_pressing_next_episode_after_the_finish_plays_the_waiting_episode():
    client = client_in_call()
    session = started_session(client, episode_for_watch())
    with Patched((jellyfin_core, "next_episode", lambda item, user_id=None: episode_for_watch("e2", 2)),
                 (jellyfin_core, "JELLYFIN_NEXT_WAIT_SECONDS", 999)):
        asyncio.run(session._handle_finished())
        from test_panel import press
        press(client, session, "jf:next")
    assert session.item_id == "e2" and session.waiting_next is None and not session.paused
    assert "next up" not in client.edited[-1]["content"] and client.edited[-1]["content"].endswith("720p")
    session_registry.clear()


def test_a_finished_episode_with_no_press_leaves_after_the_wait():
    client = client_in_call()
    session = started_session(client, episode_for_watch())
    with Patched((jellyfin_core, "next_episode", lambda item, user_id=None: episode_for_watch("e2", 2)),
                 (jellyfin_core, "JELLYFIN_NEXT_WAIT_SECONDS", 0.01)):
        async def go():
            await session._handle_finished()
            await asyncio.sleep(0.1)

        asyncio.run(go())
    assert session.finished and session.voice_session.left
    assert client.sent[-1]["content"] == "finished playing **Pilot 1**."
    session_registry.clear()


def test_autoplay_moves_on_to_the_next_episode_without_a_press():
    client = client_in_call()
    session = started_session(client, episode_for_watch())
    with Patched((jellyfin_core, "next_episode", lambda item, user_id=None: episode_for_watch("e2", 2)),
                 (jellyfin_core, "JELLYFIN_AUTOPLAY_NEXT", True)):
        finish(session)
    assert session.item_id == "e2" and not session.finished and not session.voice_session.left
    session_registry.clear()


def test_a_finished_movie_or_finale_leaves_the_call_as_before():
    client = client_in_call()
    session = started_session(client)
    finish(session)
    assert session.finished and session.voice_session.left
    assert client.sent[-1]["content"] == "finished playing **Inception**."
    session_registry.clear()


def test_pause_while_between_episodes_is_refused_privately():
    client = client_in_call()
    session = started_session(client, episode_for_watch())
    with Patched((jellyfin_core, "next_episode", lambda item, user_id=None: episode_for_watch("e2", 2)),
                 (jellyfin_core, "JELLYFIN_NEXT_WAIT_SECONDS", 999)):
        asyncio.run(session._handle_finished())
        from test_panel import press
        press(client, session, "jf:toggle")
    assert "press Next episode" in client.ephemerals[-1]["content"]
    session_registry.clear()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
