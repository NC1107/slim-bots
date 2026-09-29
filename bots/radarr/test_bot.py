#!/usr/bin/env python3
"""History dedupe, posting, and command tests with the Radarr API faked; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("RADARR_URL", "http://radarr:7878")
os.environ.setdefault("RADARR_API_KEY", "fake-key")

import bot as radarr  # noqa: E402
import radarr_core as core  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]


def record(rid, kind, tmdb=100, title="Movie", year=2020, quality="WEBDL-1080p", data=None, source="Movie.2020"):
    return {
        "id": rid, "eventType": kind, "movieId": tmdb, "sourceTitle": source, "quality": {"quality": {"name": quality}},
        "data": data or {}, "movie": {"title": title, "year": year, "tmdbId": tmdb, "imdbId": f"tt{tmdb}"},
    }


def fresh_db():
    conn = sqlite3.connect(":memory:")
    core.init_db(conn)
    return conn


def texts(conn, records):
    return [core.render_text(p) for p in core.build_posts(core.plan_events(conn, records))]


def announce(conn, records):
    """Plans, then records the marks the way a landed post does."""
    posts = core.build_posts(core.plan_events(conn, records))
    for post in posts:
        core.mark_announced(conn, post["marks"])
    return [core.render_text(p) for p in posts]


def test_a_new_import_reads_as_downloaded():
    assert texts(fresh_db(), [record(1, "downloadFolderImported")]) == ["Downloaded: Movie (2020) [WEBDL-1080p]"]


def test_a_bulk_import_is_one_summary_not_one_post_per_movie():
    burst = [record(i, "downloadFolderImported", tmdb=i, title=f"M{i}") for i in range(1, 9)]
    assert texts(fresh_db(), burst) == ["Downloaded: 8 movies (M1 (2020), M2 (2020), M3 (2020), M4 (2020), M5 (2020), ...)"]


def test_a_few_imports_stay_separate_posts():
    few = [record(i, "downloadFolderImported", tmdb=i, title=f"M{i}") for i in (1, 2)]
    assert len(texts(fresh_db(), few)) == 2


def test_a_replaced_file_of_the_same_quality_does_not_repost():
    conn = fresh_db()
    assert len(announce(conn, [record(1, "downloadFolderImported")])) == 1
    assert announce(conn, [record(2, "downloadFolderImported")]) == []


def test_the_same_movie_under_a_new_radarr_id_is_still_the_same_movie():
    conn = fresh_db()
    announce(conn, [record(1, "downloadFolderImported")])
    readded = record(2, "downloadFolderImported")
    readded["movieId"] = 999
    assert announce(conn, [readded]) == []


def test_a_better_quality_import_is_one_upgrade_line_and_then_silent():
    conn = fresh_db()
    announce(conn, [record(1, "downloadFolderImported")])
    assert announce(conn, [record(2, "downloadFolderImported", quality="Bluray-1080p")]) == ["Upgraded: Movie (2020) [Bluray-1080p]"]
    assert announce(conn, [record(3, "downloadFolderImported", quality="Bluray-1080p")]) == []


def test_an_import_after_an_upgrade_delete_reads_as_upgraded_even_when_never_seen_before():
    batch = [record(1, "movieFileDeleted", data={"reason": "Upgrade"}), record(2, "downloadFolderImported", quality="Bluray-1080p")]
    assert texts(fresh_db(), batch)[0].startswith("Upgraded:")


def test_a_manual_delete_is_not_an_upgrade():
    batch = [record(1, "movieFileDeleted", data={"reason": "Manual"}), record(2, "downloadFolderImported")]
    assert texts(fresh_db(), batch)[0].startswith("Downloaded:")


def test_the_same_grab_inside_the_window_is_dropped_and_a_late_one_is_kept():
    conn = fresh_db()
    assert len(announce(conn, [record(1, "grabbed", data={"indexer": "NZBgeek"})])) == 1
    assert announce(conn, [record(2, "grabbed")]) == []
    conn.execute("UPDATE announced SET at = at - ?", (core.GRAB_WINDOW_SECONDS + 5,))
    assert len(announce(conn, [record(3, "grabbed")])) == 1


def test_a_grab_names_its_indexer_and_a_failure_names_why():
    conn = fresh_db()
    assert announce(conn, [record(1, "grabbed", data={"indexer": "NZBgeek"})]) == ["Grabbed: Movie (2020) [WEBDL-1080p] from NZBgeek"]
    failed = record(2, "downloadFailed", data={"message": "Aborted, cannot be completed"}, source="Movie.x")
    assert announce(conn, [failed]) == ["Failed: Movie (2020) - Aborted, cannot be completed"]
    assert announce(conn, [failed]) == []


def test_records_missing_their_movie_are_skipped_not_crashed_on():
    broken = record(1, "grabbed")
    broken["movie"] = None
    assert texts(fresh_db(), [broken]) == []


def test_cursor_only_moves_forward():
    conn = fresh_db()
    core.advance_cursor(conn, 50)
    core.advance_cursor(conn, 9)
    assert core.get_cursor(conn) == 50


class FakeRadarr:
    """Routes `core.api` calls by path; records every call so a test can assert what was sent."""

    def __init__(self, history=None):
        self.history = history or []
        self.calls = []
        self.lookup = []
        self.queue = {"records": [], "totalRecords": 0}
        self.calendar = []
        self.fail_with = None
        self.refuse_posts = None

    def __call__(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if self.fail_with:
            raise self.fail_with
        if path == "/history":
            size = params.get("pageSize", 100)
            page = sorted(self.history, key=lambda r: -r["id"])[(params.get("page", 1) - 1) * size:][:size]
            return {"records": page}
        routes = {
            "/movie/lookup": self.lookup, "/queue": self.queue, "/calendar": self.calendar,
            "/qualityprofile": [{"id": 4, "name": "HD-1080p"}], "/rootfolder": [{"path": "/media/movies"}],
        }
        if method == "POST" and path == "/movie":
            if self.refuse_posts:
                raise self.refuse_posts
            return {"id": 99}
        return routes[path]


class Patched:
    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        self.saved = core.api
        core.api = self.fake
        return self.fake

    def __exit__(self, *_exc):
        core.api = self.saved


def setup():
    radarr.bot.store = Store(":memory:", migrate=core.init_db)
    asyncio.run(radarr.bot.store.open())
    radarr.bot.channels = {"c1"}
    core._command_cooldown._last.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    client.edited = []

    async def record_edit(channel_id, message_id, content):
        client.edited.append({"channel_id": channel_id, "message_id": message_id, "content": content})

    client.edit_message = record_edit
    radarr.bot.client = client
    radarr.bot.space = Space(client)
    radarr.bot.authors = AuthorFilter(client, space=radarr.bot.space, ignore_bots=True)
    radarr.bot.me_id = "bot-1"
    asyncio.run(radarr.bot.space.refresh_members())
    return client


def message(content, msg_id="m1"):
    return {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content}


def process(*messages):
    async def run():
        for msg in messages:
            await radarr.bot.process_message(msg)

    asyncio.run(run())


def poll(fake):
    with Patched(fake):
        asyncio.run(radarr.poll_once())


def test_the_first_poll_starts_at_the_newest_record_and_announces_nothing():
    client = setup()
    fake = FakeRadarr([record(7, "downloadFolderImported", tmdb=7)])
    poll(fake)
    assert client.sent == []
    assert asyncio.run(radarr.bot.store.run(core.get_cursor)) == 7


def test_a_new_import_after_bootstrap_is_posted_once_with_a_stable_id():
    client = setup()
    fake = FakeRadarr([record(7, "downloadFolderImported", tmdb=7)])
    poll(fake)
    fake.history.append(record(8, "downloadFolderImported", tmdb=8, title="Second"))
    poll(fake)
    poll(fake)
    assert len(client.sent) == 1
    assert "Second (2020)" in client.sent[0]["embeds"][0]["title"]
    assert client.sent[0]["channel_id"] == "c1"


def test_a_failed_send_retries_without_losing_or_repeating_posts():
    client = setup()
    fake = FakeRadarr([record(1, "downloadFolderImported")])
    poll(fake)
    fake.history.extend([record(2, "downloadFolderImported", tmdb=2, title="Two"), record(3, "downloadFolderImported", tmdb=3, title="Three")])
    real_send, attempts = client.send, []

    async def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 2:
            raise radarr.ApiError(500, "boom")
        return await real_send(*args, **kwargs)

    client.send = flaky
    poll(fake)
    client.send = real_send
    poll(fake)
    titles = [m["embeds"][0]["title"] for m in client.sent]
    assert len(titles) == 2 and len(set(titles)) == 2


def test_a_radarr_outage_during_a_poll_raises_for_the_loop_to_retry():
    setup()
    fake = FakeRadarr([record(1, "grabbed")])
    poll(fake)
    fake.fail_with = OSError("down")
    try:
        poll(fake)
    except OSError:
        return
    raise AssertionError("expected the outage to propagate")


def test_search_marks_what_is_already_in_the_library():
    client = setup()
    fake = FakeRadarr()
    fake.lookup = [{"title": "Dune", "year": 2021, "tmdbId": 1, "id": 5}, {"title": "Dune Part Two", "year": 2024, "tmdbId": 2}]
    with Patched(fake):
        process(message("!radarr search dune"))
    reply = client.sent[-1]["content"]
    assert "Dune (2021) - in the library" in reply and "- Dune Part Two (2024)" in reply


def test_search_with_no_matches_and_with_no_query_answer_plainly():
    client = setup()
    fake = FakeRadarr()
    with Patched(fake):
        process(message("!radarr search zzzz"))
        core._command_cooldown._last.clear()
        process(message("!radarr search", "m2"))
    assert 'nothing found for "zzzz"' in client.sent[0]["content"]
    assert "a movie name must be between 1 and 100" in client.sent[-1]["content"]


def test_search_is_cooldown_limited():
    client = setup()
    fake = FakeRadarr()
    fake.lookup = [{"title": "A", "year": 2000, "tmdbId": 1}]
    with Patched(fake):
        process(message("!radarr search a"), message("!radarr search a", "m2"))
    assert "try again" in client.sent[-1]["content"]


def test_an_oversized_query_is_refused_before_touching_radarr():
    client = setup()
    fake = FakeRadarr()
    with Patched(fake):
        process(message("!radarr search " + "x" * 200))
    assert "between" in client.sent[-1]["content"] and fake.calls == []


def test_an_unreachable_radarr_answers_with_a_sentence_not_a_traceback():
    client = setup()
    fake = FakeRadarr()
    fake.fail_with = OSError("refused")
    with Patched(fake):
        process(message("!radarr queue"))
    assert "unavailable right now" in client.sent[-1]["content"]


def test_a_rejected_api_key_says_so():
    client = setup()
    fake = FakeRadarr()
    fake.fail_with = core.RadarrAuthError("no")
    with Patched(fake):
        process(message("!radarr queue"))
    assert "api key" in client.sent[-1]["content"]


def test_queue_lists_downloads_and_an_empty_queue_says_so():
    client = setup()
    fake = FakeRadarr()
    with Patched(fake):
        process(message("!radarr queue"))
        assert "empty" in client.sent[-1]["content"]
        core._command_cooldown._last.clear()
        fake.queue = {"records": [{"title": "x", "status": "downloading", "timeleft": "00:10:00", "movie": {"title": "Dune", "year": 2021}}], "totalRecords": 12}
        process(message("!radarr queue", "m2"))
    reply = client.sent[-1]["content"]
    assert "Dune (2021) - downloading, 00:10:00 left" in reply and "11 more" in reply


def test_calendar_lists_releases_by_the_date_inside_the_window_and_bounds_its_days():
    client = setup()
    fake = FakeRadarr()
    today = core.calendar_window(1)[0]
    fake.calendar = [{"title": "Dune", "year": 2021, "inCinemas": "2000-01-01T00:00:00Z", "digitalRelease": f"{today}T00:00:00Z", "hasFile": False}]
    with Patched(fake):
        process(message("!radarr calendar 3"))
        core._command_cooldown._last.clear()
        process(message("!radarr calendar 99", "m2"))
    assert f"- {today} Dune (2021)" in client.sent[0]["content"]
    assert "at most" in client.sent[-1]["content"]


def test_plaintext_is_allowed_only_to_loopback_or_a_bare_service_name():
    saved = core.RADARR_URL
    try:
        for url, ok in (("http://radarr:7878", True), ("http://127.0.0.1:7878", True), ("http://radarr.example.com", False), ("https://radarr.example.com", True)):
            core.RADARR_URL = url
            assert (core.check_radarr_config() is None) is ok, url
    finally:
        core.RADARR_URL = saved


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
