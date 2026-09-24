#!/usr/bin/env python3
"""Unit tests for bot.py's grouping, rendering, filtering, and command
handling, using slimbots.testing.FakeClient and monkeypatched Jellyfin calls
so none of this needs a live deployment or a real Jellyfin server.

Run it directly, no test framework needed:

    pip install -r requirements.txt
    python3 test_bot.py
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402
from slimbots import AuthorFilter  # noqa: E402
from slimbots import limits  # noqa: E402
from slimbots.testing import FakeClient  # noqa: E402

ME = "bot-1"
USER = "user-1"
OTHER_BOT = "bot-2"
bot.SLIMM_COMMAND_CHANNEL = "chan-cmd"


def movie(item_id, name, date_created, genres=None, library_id=None):
    item = {
        "Id": item_id,
        "Type": "Movie",
        "Name": name,
        "DateCreated": date_created,
        "ProductionYear": 2020,
        "Overview": "",
        "Genres": genres or [],
    }
    if library_id is not None:
        item["_library_id"] = library_id
    return item


def episode(item_id, series_id, series_name, season, index, date_created):
    return {
        "Id": item_id,
        "Type": "Episode",
        "Name": f"Episode {index}",
        "SeriesId": series_id,
        "SeriesName": series_name,
        "SeasonName": season,
        "IndexNumber": index,
        "DateCreated": date_created,
    }


def fresh_client():
    client = FakeClient(me_id=ME)
    client.respond("GET", f"/users/{USER}", {"is_bot": False})
    client.respond("GET", f"/users/{OTHER_BOT}", {"is_bot": True})
    return client, AuthorFilter(client)


def incoming(content, author=USER, message_id="m1"):
    return {"author_id": author, "content": content, "id": message_id}


# --- grouping and rendering ---


def test_single_movie_renders_title_and_overview():
    item = movie("m1", "Inception", "2026-01-01T00:00:00.0000000Z")
    item["Overview"] = "a heist movie"
    card = bot.render_single_post(item)
    assert card["title"] == "Movie added: Inception (2020)"
    assert bot.render_text(card) == "Movie added: Inception (2020)\na heist movie"


def test_single_episode_has_no_description():
    ep = episode("e1", "s1", "Breaking Bad", "Season 1", 1, "2026-01-01T00:00:00.0000000Z")
    card = bot.render_series_post([ep])
    assert "New episode: Breaking Bad" in card["title"]
    assert bot.render_text(card) == card["title"]


def test_multi_episode_series_groups_by_season():
    eps = [
        episode("e1", "s1", "Breaking Bad", "Season 1", 1, "2026-01-01T00:00:00.0000000Z"),
        episode("e2", "s1", "Breaking Bad", "Season 1", 2, "2026-01-01T00:00:01.0000000Z"),
    ]
    card = bot.render_series_post(eps)
    assert card["title"] == "Breaking Bad: 2 new episodes"
    assert "Season 1: 2 (episodes 1-2)" in card["description"]


def test_collapsed_post_over_threshold():
    items = [movie(f"m{i}", f"Movie {i}", "2026-01-01T00:00:00.0000000Z") for i in range(bot.JELLYFIN_BATCH_THRESHOLD + 1)]
    posts = bot.build_posts(items)
    assert len(posts) == 1
    assert "added:" in posts[0]["title"]


def test_build_posts_keeps_singles_separate_under_threshold():
    items = [movie("m1", "A", "2026-01-01T00:00:00.0000000Z"), movie("m2", "B", "2026-01-01T00:00:01.0000000Z")]
    posts = bot.build_posts(items)
    assert len(posts) == 2


# --- genre filtering ---


def test_excluded_genre_is_detected_case_insensitively():
    bot.JELLYFIN_EXCLUDE_GENRES = {"horror"}
    try:
        assert bot.is_excluded(movie("m1", "Scary", "2026-01-01T00:00:00.0000000Z", genres=["Horror", "Thriller"]))
        assert not bot.is_excluded(movie("m2", "Fine", "2026-01-01T00:00:00.0000000Z", genres=["Comedy"]))
    finally:
        bot.JELLYFIN_EXCLUDE_GENRES = set()


def test_no_exclusion_list_excludes_nothing():
    bot.JELLYFIN_EXCLUDE_GENRES = set()
    assert not bot.is_excluded(movie("m1", "Anything", "2026-01-01T00:00:00.0000000Z", genres=["Horror"]))


# --- library routing ---


def test_target_channel_uses_route_when_present():
    bot.JELLYFIN_LIBRARY_ROUTES = {"lib-1": "chan-movies"}
    try:
        assert bot.target_channel("lib-1") == "chan-movies"
        assert bot.target_channel("lib-2") == bot.SLIMM_CHANNEL
        assert bot.target_channel(None) == bot.SLIMM_CHANNEL
    finally:
        bot.JELLYFIN_LIBRARY_ROUTES = {}


def test_parse_library_routes():
    assert bot.parse_library_routes("a:x,b:y") == {"a": "x", "b": "y"}
    assert bot.parse_library_routes("") == {}


# --- commands ---


def test_search_ignores_another_bot():
    client, authors = fresh_client()
    cooldown = limits.Cooldown(1000)
    bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin search inception", author=OTHER_BOT))
    assert client.sent == []


def test_search_rejects_an_overlong_query():
    client, authors = fresh_client()
    cooldown = limits.Cooldown(1000)
    bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin search " + "x" * (bot.MAX_QUERY_LENGTH + 1)))
    assert "too long" in client.sent[0]["content"]


def test_search_reports_results():
    client, authors = fresh_client()
    cooldown = limits.Cooldown(1000)
    with mock.patch.object(bot, "search_items", return_value=[{"Name": "Inception", "ProductionYear": 2010, "Type": "Movie"}]):
        bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin search inception"))
    assert "Inception (2010) [Movie]" in client.sent[0]["content"]


def test_search_reports_no_results():
    client, authors = fresh_client()
    cooldown = limits.Cooldown(1000)
    with mock.patch.object(bot, "search_items", return_value=[]):
        bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin search nothingatall"))
    assert "nothing found" in client.sent[0]["content"]


def test_search_is_cooled_down_per_user():
    client, authors = fresh_client()
    cooldown = limits.Cooldown(1000)
    with mock.patch.object(bot, "search_items", return_value=[]):
        bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin search a", message_id="m1"))
        bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin search b", message_id="m2"))
    assert "slow down" in client.sent[1]["content"]


def test_recent_rejects_an_out_of_range_day_count():
    client, authors = fresh_client()
    cooldown = limits.Cooldown(1000)
    bot.handle_command(client, authors, cooldown, ME, incoming(f"!jellyfin recent {bot.RECENT_MAX_DAYS + 1}"))
    assert "must be at most" in client.sent[0]["content"]


def test_recent_summarizes_by_type():
    client, authors = fresh_client()
    cooldown = limits.Cooldown(1000)
    items = [movie("m1", "A", "x"), movie("m2", "B", "x"), episode("e1", "s1", "Show", "S1", 1, "x")]
    with mock.patch.object(bot, "items_since", return_value=items):
        bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin recent"))
    reply = client.sent[0]["content"]
    assert "2 Movie" in reply
    assert "1 Episode" in reply


def test_help_and_unknown_subcommand_both_get_help_text():
    client, authors = fresh_client()
    cooldown = limits.Cooldown(1000)
    bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin help", message_id="m1"))
    bot.handle_command(client, authors, cooldown, ME, incoming("!jellyfin whatever", message_id="m2"))
    assert client.sent[0]["content"] == bot.HELP_TEXT
    assert client.sent[1]["content"] == bot.HELP_TEXT


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
