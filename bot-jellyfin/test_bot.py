#!/usr/bin/env python3
"""Grouping/cursor unit tests, plus command-layer tests against FakeAsyncClient with jf_get monkeypatched.

Run it directly, no test framework needed: python3 test_bot.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JELLYFIN_URL", "https://fake-jellyfin.invalid")
os.environ.setdefault("JELLYFIN_API_KEY", "fake-key")

import bot as jellyfin  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]


def episode(series_id, series_name, season, index, created="2024-01-01T00:00:00Z", eid=None):
    return {
        "Id": eid or f"ep-{series_id}-{season}-{index}", "Type": "Episode", "Name": f"Episode {index}",
        "SeriesId": series_id, "SeriesName": series_name, "SeasonName": season, "IndexNumber": index,
        "DateCreated": created, "_library_id": None,
    }


def movie(item_id, name, created="2024-01-01T00:00:00Z"):
    return {"Id": item_id, "Type": "Movie", "Name": name, "ProductionYear": 2020, "DateCreated": created, "_library_id": None}


def test_a_single_new_episode_posts_as_one_episode_card():
    posts = jellyfin.build_posts([episode("s1", "Show", "Season 1", 3)])
    assert len(posts) == 1
    assert "Episode 3" not in posts[0]["title"] or "New episode" in posts[0]["title"]


def test_multiple_new_episodes_group_by_series():
    items = [episode("s1", "Show", "Season 1", i) for i in (1, 2, 3)]
    posts = jellyfin.build_posts(items)
    assert len(posts) == 1
    assert "3 new episodes" in posts[0]["title"]


def test_movies_over_the_batch_threshold_collapse():
    items = [movie(f"m{i}", f"Movie {i}") for i in range(jellyfin.JELLYFIN_BATCH_THRESHOLD + 2)]
    posts = jellyfin.build_posts(items)
    assert len(posts) == 1
    assert "added" in posts[0]["title"]


def test_movies_under_the_batch_threshold_post_individually():
    items = [movie("m1", "Movie One"), movie("m2", "Movie Two")]
    posts = jellyfin.build_posts(items)
    assert len(posts) == 2


def test_cursor_only_moves_forward():
    conn = jellyfin.sqlite3.connect(":memory:")
    jellyfin.init_db(conn)
    jellyfin.advance_cursor(conn, "2024-06-01T00:00:00.0000000Z")
    jellyfin.advance_cursor(conn, "2024-01-01T00:00:00.0000000Z")
    assert jellyfin.get_cursor(conn) == "2024-06-01T00:00:00.0000000Z"


def test_already_posted_items_are_filtered_before_grouping():
    conn = jellyfin.sqlite3.connect(":memory:")
    jellyfin.init_db(conn)
    jellyfin.advance_cursor(conn, "2000-01-01T00:00:00.0000000Z")
    jellyfin.mark_posted(conn, ["m1"])
    original = jellyfin.items_since
    jellyfin.items_since = lambda cursor: [movie("m1", "Old"), movie("m2", "New")]
    try:
        fresh = jellyfin.fetch_new_items(conn)
    finally:
        jellyfin.items_since = original
    assert [item["Id"] for item in fresh] == ["m2"]


def test_excluded_genre_is_dropped():
    jellyfin.JELLYFIN_EXCLUDE_GENRES.add("horror")
    try:
        item = {**movie("m1", "Scary"), "Genres": ["Horror"]}
        assert jellyfin.is_excluded(item)
        item2 = {**movie("m2", "Not Scary"), "Genres": ["Comedy"]}
        assert not jellyfin.is_excluded(item2)
    finally:
        jellyfin.JELLYFIN_EXCLUDE_GENRES.discard("horror")


def test_library_routes_fall_back_to_the_default_channel():
    jellyfin.JELLYFIN_LIBRARY_ROUTES["lib-1"] = "routed-channel"
    jellyfin.bot.channels = {"default-channel"}
    try:
        assert jellyfin.target_channel("lib-1") == "routed-channel"
        assert jellyfin.target_channel("lib-2") == "default-channel"
    finally:
        jellyfin.JELLYFIN_LIBRARY_ROUTES.pop("lib-1", None)


def message(content, msg_id="m1"):
    return {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content}


def setup():
    jellyfin.bot.db = jellyfin.sqlite3.connect(":memory:")
    jellyfin.init_db(jellyfin.bot.db)
    jellyfin.bot.channels = {"c1"}
    jellyfin._command_cooldown._last.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    jellyfin.bot.client = client
    jellyfin.bot.space = Space(client)
    jellyfin.bot.authors = AuthorFilter(client, space=jellyfin.bot.space, ignore_bots=True)
    jellyfin.bot.me_id = "bot-1"
    asyncio.run(jellyfin.bot.space.refresh_members())
    return client


def process(client, *messages):
    async def run():
        for msg in messages:
            await jellyfin.bot.process_message(msg)

    asyncio.run(run())


def _patch_search_items(items):
    original = jellyfin.search_items
    jellyfin.search_items = lambda query, limit: items
    return original


def test_search_returns_matching_items():
    client = setup()
    original = _patch_search_items([movie("m1", "Inception")])
    try:
        process(client, message("!jellyfin search inception"))
    finally:
        jellyfin.search_items = original
    assert "Inception" in client.sent[-1]["content"]


def test_search_with_no_query_shows_help():
    client = setup()
    process(client, message("!jellyfin search"))
    assert "commands:" in client.sent[-1]["content"]


def test_search_is_cooldown_limited():
    client = setup()
    original = _patch_search_items([movie("m1", "Inception")])
    try:
        process(client, message("!jellyfin search inception", "m1"))
        process(client, message("!jellyfin search inception", "m2"))
    finally:
        jellyfin.search_items = original
    assert "try again" in client.sent[-1]["content"]


def test_recent_reports_a_summary():
    client = setup()
    original = jellyfin.items_since
    jellyfin.items_since = lambda cutoff: [movie("m1", "A"), movie("m2", "B")]
    try:
        process(client, message("!jellyfin recent 3"))
    finally:
        jellyfin.items_since = original
    assert "2 item(s)" in client.sent[-1]["content"]


def test_recent_rejects_an_out_of_range_day_count():
    client = setup()
    process(client, message("!jellyfin recent 999"))
    assert "at most" in client.sent[-1]["content"]


def test_unrecognised_subcommand_shows_help():
    client = setup()
    process(client, message("!jellyfin nonsense"))
    assert "commands:" in client.sent[-1]["content"]


def test_another_bot_is_ignored_by_default():
    client = setup()
    client.respond(
        "GET", "/members",
        MEMBERS + [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []}],
    )
    asyncio.run(jellyfin.bot.space.refresh_members())
    process(client, {"id": "m1", "author_id": "bot-2", "channel_id": "c1", "content": "!jellyfin help"})
    assert client.sent == []


def test_poll_loop_propagates_a_terminal_jellyfin_auth_error():
    async def boom():
        raise jellyfin.JellyfinAuthError("nope")

    original = jellyfin.poll_once
    jellyfin.poll_once = boom
    try:
        raised = False
        try:
            asyncio.run(jellyfin.poll_loop())
        except jellyfin.JellyfinAuthError:
            raised = True
        assert raised
    finally:
        jellyfin.poll_once = original


def test_on_connect_registers_poll_loop_as_a_supervised_background_task():
    setup()
    jellyfin._background_started = False
    original = jellyfin.bootstrap_cursor
    jellyfin.bootstrap_cursor = lambda conn: None

    async def run():
        await jellyfin.on_connect()
        assert len(jellyfin.bot._background_tasks) == 1
        task = next(iter(jellyfin.bot._background_tasks))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(run())
    finally:
        jellyfin.bootstrap_cursor = original


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
