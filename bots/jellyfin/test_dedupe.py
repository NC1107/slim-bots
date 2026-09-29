#!/usr/bin/env python3
"""A replaced file (new item id, same media) is not announced again; run directly: python3 test_dedupe.py."""

import asyncio
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JELLYFIN_URL", "https://fake-jellyfin.invalid")
os.environ.setdefault("JELLYFIN_API_KEY", "fake-key")

import bot as jellyfin  # noqa: E402

core = jellyfin.jellyfin_core
DAY = 86400


def episode(eid, series="s1", season=1, index=1, created="2024-01-01T00:00:00Z"):
    return {
        "Id": eid, "Type": "Episode", "Name": f"Episode {index}", "SeriesId": series, "SeriesName": "Show",
        "ParentIndexNumber": season, "IndexNumber": index, "DateCreated": created, "_library_id": None,
    }


def movie(eid, created="2024-01-01T00:00:00Z", providers=None, name="Inception", year=2010):
    return {
        "Id": eid, "Type": "Movie", "Name": name, "ProductionYear": year, "ProviderIds": providers or {},
        "DateCreated": created, "_library_id": None,
    }


class FakeStore:
    def __init__(self, conn):
        self.conn = conn

    async def run(self, fn, *args, **kwargs):
        return fn(self.conn, *args, **kwargs)


class Feed:
    """A fake recent-items feed plus the real poll_once, recording what would be posted."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        core.init_db(self.conn)
        core.advance_cursor(self.conn, "2000-01-01T00:00:00.0000000Z")
        self.items = []
        self.sent = []

    def poll(self):
        async def send_post(entry):
            self.sent.append(entry["item_ids"])

        saved = (core.items_since, jellyfin.send_post, jellyfin.bot.store)
        core.items_since = lambda cursor: [i for i in self.items if i["DateCreated"] >= cursor]
        jellyfin.send_post = send_post
        jellyfin.bot.store = FakeStore(self.conn)
        try:
            asyncio.run(jellyfin.poll_once())
        finally:
            core.items_since, jellyfin.send_post, jellyfin.bot.store = saved
        return self.sent


def with_settings(days=7, reannounce=False):
    core.JELLYFIN_DEDUPE_DAYS, core.JELLYFIN_REANNOUNCE_REPLACED = days, reannounce


def test_a_replaced_episode_is_not_reannounced():
    with_settings()
    feed = Feed()
    feed.items = [episode("old")]
    feed.poll()
    feed.items.append(episode("new", created="2024-01-02T00:00:00Z"))
    assert feed.poll() == [["old"]]


def test_a_replaced_movie_is_not_reannounced_by_provider_id():
    with_settings()
    feed = Feed()
    feed.items = [movie("old", providers={"Tmdb": "27205"})]
    feed.poll()
    feed.items.append(movie("new", created="2024-01-02T00:00:00Z", providers={"tmdb": "27205"}, name="Inception (Remux)"))
    assert feed.poll() == [["old"]]


def test_a_replaced_movie_without_provider_ids_matches_on_name_and_year():
    with_settings()
    feed = Feed()
    feed.items = [movie("old")]
    feed.poll()
    feed.items.append(movie("new", created="2024-01-02T00:00:00Z", name=" INCEPTION "))
    assert feed.poll() == [["old"]]


def test_a_different_movie_with_the_same_name_and_another_year_still_posts():
    with_settings()
    feed = Feed()
    feed.items = [movie("old")]
    feed.poll()
    feed.items.append(movie("remake", created="2024-01-02T00:00:00Z", year=2025))
    assert feed.poll() == [["old"], ["remake"]]


def test_a_genuinely_new_episode_still_posts():
    with_settings()
    feed = Feed()
    feed.items = [episode("e1", index=1)]
    feed.poll()
    feed.items.append(episode("e2", index=2, created="2024-01-02T00:00:00Z"))
    assert feed.poll() == [["e1"], ["e2"]]


def test_a_replacement_stays_quiet_after_the_window_passes():
    with_settings()
    feed = Feed()
    feed.items = [episode("old")]
    feed.poll()
    feed.items.append(episode("new", created="2024-01-02T00:00:00Z"))
    feed.poll()
    real = time.time
    time.time = lambda: real() + 30 * DAY
    try:
        assert feed.poll() == [["old"]]
    finally:
        time.time = real


def test_the_same_media_posts_again_once_the_window_has_expired():
    with_settings()
    feed = Feed()
    feed.items = [episode("old")]
    feed.poll()
    real = time.time
    time.time = lambda: real() + 8 * DAY
    try:
        feed.items.append(episode("new", created="2024-01-02T00:00:00Z"))
        assert feed.poll() == [["old"], ["new"]]
    finally:
        time.time = real


def test_a_zero_day_window_turns_dedupe_off():
    with_settings(days=0)
    feed = Feed()
    feed.items = [episode("old")]
    feed.poll()
    feed.items.append(episode("new", created="2024-01-02T00:00:00Z"))
    assert feed.poll() == [["old"], ["new"]]


def test_the_opt_in_setting_reannounces_a_replaced_episode():
    with_settings(reannounce=True)
    feed = Feed()
    feed.items = [episode("old")]
    feed.poll()
    feed.items.append(episode("new", created="2024-01-02T00:00:00Z"))
    assert feed.poll() == [["old"], ["new"]]


def test_two_copies_arriving_in_one_poll_post_once():
    with_settings()
    feed = Feed()
    feed.items = [episode("a"), episode("b", created="2024-01-01T00:00:01Z")]
    assert feed.poll() == [["a"]]


def test_the_setting_reads_from_the_environment_style_names():
    class FakeBot:
        def setting(self, name, default=None, type=None, required=False):
            return {"JELLYFIN_DEDUPE_DAYS": 3, "JELLYFIN_REANNOUNCE_REPLACED": "true"}.get(name, default)

    saved = (core.JELLYFIN_URL, core.JELLYFIN_API_KEY)
    try:
        core.configure(FakeBot())
        assert (core.JELLYFIN_DEDUPE_DAYS, core.JELLYFIN_REANNOUNCE_REPLACED) == (3, True)
    finally:
        core.JELLYFIN_URL, core.JELLYFIN_API_KEY = saved
        with_settings()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
