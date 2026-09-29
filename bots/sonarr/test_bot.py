#!/usr/bin/env python3
"""History dedupe, posting, and command tests with the Sonarr API faked; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SONARR_URL", "http://sonarr:8989")
os.environ.setdefault("SONARR_API_KEY", "fake-key")

import bot as sonarr  # noqa: E402
import sonarr_core as core  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]


def record(rid, kind, episode=1, season=1, series_id=1, quality="WEBDL-1080p", data=None, source="Show.S01E01"):
    return {
        "id": rid, "eventType": kind, "seriesId": series_id, "episodeId": series_id * 1000 + season * 100 + episode,
        "sourceTitle": source, "quality": {"quality": {"name": quality}}, "data": data or {},
        "series": {"title": "Show", "year": 2020}, "episode": {"seasonNumber": season, "episodeNumber": episode, "title": f"Ep {episode}"},
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
    assert texts(fresh_db(), [record(1, "downloadFolderImported")]) == ["Downloaded: Show (2020) S01E01 - Ep 1 [WEBDL-1080p]"]


def test_a_season_pack_is_one_post_not_one_per_episode():
    pack = [record(i, "downloadFolderImported", episode=i) for i in range(1, 9)]
    assert texts(fresh_db(), pack) == ["Downloaded: Show (2020) S01, 8 episodes (E01-E08) [WEBDL-1080p]"]


def test_a_replaced_file_of_the_same_quality_does_not_repost():
    conn = fresh_db()
    assert len(announce(conn, [record(1, "downloadFolderImported")])) == 1
    assert announce(conn, [record(2, "downloadFolderImported")]) == []


def test_a_better_quality_import_is_one_upgrade_line_and_then_silent():
    conn = fresh_db()
    announce(conn, [record(1, "downloadFolderImported")])
    upgrade = [record(2, "downloadFolderImported", quality="Bluray-1080p")]
    assert announce(conn, upgrade) == ["Upgraded: Show (2020) S01E01 - Ep 1 [Bluray-1080p]"]
    assert announce(conn, [record(3, "downloadFolderImported", quality="Bluray-1080p")]) == []


def test_an_import_after_an_upgrade_delete_reads_as_upgraded_even_when_never_seen_before():
    batch = [record(1, "episodeFileDeleted", data={"reason": "Upgrade"}), record(2, "downloadFolderImported", quality="Bluray-1080p")]
    assert texts(fresh_db(), batch)[0].startswith("Upgraded:")


def test_the_same_grab_inside_the_window_is_dropped_and_a_late_one_is_kept():
    conn = fresh_db()
    assert len(announce(conn, [record(1, "grabbed", data={"indexer": "NZBgeek"})])) == 1
    assert announce(conn, [record(2, "grabbed")]) == []
    conn.execute("UPDATE announced SET at = at - ?", (core.GRAB_WINDOW_SECONDS + 5,))
    assert len(announce(conn, [record(3, "grabbed")])) == 1


def test_a_grab_names_its_indexer_and_a_failure_names_why():
    conn = fresh_db()
    assert announce(conn, [record(1, "grabbed", data={"indexer": "NZBgeek"})]) == ["Grabbed: Show (2020) S01E01 - Ep 1 [WEBDL-1080p] from NZBgeek"]
    failed = record(2, "downloadFailed", data={"message": "Aborted, cannot be completed"}, source="Show.S01E01.x")
    assert announce(conn, [failed]) == ["Failed: Show (2020) S01E01 - Ep 1 - Aborted, cannot be completed"]
    assert announce(conn, [failed]) == []


def test_records_missing_their_series_are_skipped_not_crashed_on():
    broken = record(1, "grabbed")
    broken["series"] = None
    assert texts(fresh_db(), [broken]) == []


def test_cursor_only_moves_forward():
    conn = fresh_db()
    core.advance_cursor(conn, 50)
    core.advance_cursor(conn, 9)
    assert core.get_cursor(conn) == 50


class FakeSonarr:
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
            "/series/lookup": self.lookup, "/queue": self.queue, "/calendar": self.calendar,
            "/qualityprofile": [{"id": 4, "name": "HD-1080p"}], "/rootfolder": [{"path": "/media/tv"}],
        }
        if method == "POST" and path == "/series":
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
    sonarr.bot.store = Store(":memory:", migrate=core.init_db)
    asyncio.run(sonarr.bot.store.open())
    sonarr.bot.channels = {"c1"}
    core._command_cooldown._last.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    client.edited = []

    async def record_edit(channel_id, message_id, content):
        client.edited.append({"channel_id": channel_id, "message_id": message_id, "content": content})

    client.edit_message = record_edit
    sonarr.bot.client = client
    sonarr.bot.space = Space(client)
    sonarr.bot.authors = AuthorFilter(client, space=sonarr.bot.space, ignore_bots=True)
    sonarr.bot.me_id = "bot-1"
    asyncio.run(sonarr.bot.space.refresh_members())
    return client


def message(content, msg_id="m1"):
    return {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content}


def process(*messages):
    async def run():
        for msg in messages:
            await sonarr.bot.process_message(msg)

    asyncio.run(run())


def poll(fake):
    with Patched(fake):
        asyncio.run(sonarr.poll_once())


def test_the_first_poll_starts_at_the_newest_record_and_announces_nothing():
    client = setup()
    fake = FakeSonarr([record(7, "downloadFolderImported")])
    poll(fake)
    assert client.sent == []
    assert asyncio.run(sonarr.bot.store.run(core.get_cursor)) == 7


def test_a_new_import_after_bootstrap_is_posted_once_with_a_stable_id():
    client = setup()
    fake = FakeSonarr([record(7, "downloadFolderImported")])
    poll(fake)
    fake.history.append(record(8, "downloadFolderImported", episode=2))
    poll(fake)
    poll(fake)
    assert len(client.sent) == 1
    assert "S01E02" in client.sent[0]["embeds"][0]["title"]
    assert client.sent[0]["channel_id"] == "c1"


def test_a_failed_send_retries_without_losing_or_repeating_posts():
    client = setup()
    fake = FakeSonarr([record(1, "downloadFolderImported")])
    poll(fake)
    fake.history.extend([record(2, "downloadFolderImported", episode=2), record(3, "downloadFolderImported", episode=3, series_id=2)])
    real_send, attempts = client.send, []

    async def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 2:
            raise sonarr.ApiError(500, "boom")
        return await real_send(*args, **kwargs)

    client.send = flaky
    poll(fake)
    client.send = real_send
    poll(fake)
    titles = [m["embeds"][0]["title"] for m in client.sent]
    assert len(titles) == 2 and len(set(titles)) == 2


def test_a_sonarr_outage_during_a_poll_raises_for_the_loop_to_retry():
    setup()
    fake = FakeSonarr([record(1, "grabbed")])
    poll(fake)
    fake.fail_with = OSError("down")
    try:
        poll(fake)
    except OSError:
        return
    raise AssertionError("expected the outage to propagate")


def test_search_marks_what_is_already_in_the_library():
    client = setup()
    fake = FakeSonarr()
    fake.lookup = [{"title": "Severance", "year": 2022, "tvdbId": 1, "id": 5}, {"title": "Severance Fan", "year": 2023, "tvdbId": 2}]
    with Patched(fake):
        process(message("!sonarr search severance"))
    reply = client.sent[-1]["content"]
    assert "Severance (2022) - in the library" in reply and "- Severance Fan (2023)" in reply


def test_search_with_no_matches_and_with_no_query_answer_plainly():
    client = setup()
    fake = FakeSonarr()
    with Patched(fake):
        process(message("!sonarr search zzzz"))
        core._command_cooldown._last.clear()
        process(message("!sonarr search", "m2"))
    assert 'nothing found for "zzzz"' in client.sent[0]["content"]
    assert "a show name must be between 1 and 100" in client.sent[-1]["content"]


def test_search_is_cooldown_limited():
    client = setup()
    fake = FakeSonarr()
    fake.lookup = [{"title": "A", "year": 2000, "tvdbId": 1}]
    with Patched(fake):
        process(message("!sonarr search a"), message("!sonarr search a", "m2"))
    assert "try again" in client.sent[-1]["content"]


def test_an_oversized_query_is_refused_before_touching_sonarr():
    client = setup()
    fake = FakeSonarr()
    with Patched(fake):
        process(message("!sonarr search " + "x" * 200))
    assert "between" in client.sent[-1]["content"] and fake.calls == []


def test_an_unreachable_sonarr_answers_with_a_sentence_not_a_traceback():
    client = setup()
    fake = FakeSonarr()
    fake.fail_with = OSError("refused")
    with Patched(fake):
        process(message("!sonarr queue"))
    assert "unavailable right now" in client.sent[-1]["content"]


def test_a_rejected_api_key_says_so():
    client = setup()
    fake = FakeSonarr()
    fake.fail_with = core.SonarrAuthError("no")
    with Patched(fake):
        process(message("!sonarr queue"))
    assert "api key" in client.sent[-1]["content"]


def test_queue_lists_downloads_and_an_empty_queue_says_so():
    client = setup()
    fake = FakeSonarr()
    with Patched(fake):
        process(message("!sonarr queue"))
        assert "empty" in client.sent[-1]["content"]
        core._command_cooldown._last.clear()
        fake.queue = {"records": [{"title": "x", "status": "downloading", "timeleft": "00:10:00", "series": {"title": "Show"}, "episode": {"seasonNumber": 2, "episodeNumber": 3}}], "totalRecords": 12}
        process(message("!sonarr queue", "m2"))
    reply = client.sent[-1]["content"]
    assert "Show S02E03 - downloading, 00:10:00 left" in reply and "11 more" in reply


def test_calendar_lists_upcoming_episodes_and_bounds_its_days():
    client = setup()
    fake = FakeSonarr()
    fake.calendar = [{"airDateUtc": "2026-10-01T01:00:00Z", "seasonNumber": 1, "episodeNumber": 4, "series": {"title": "Show"}, "hasFile": False}]
    with Patched(fake):
        process(message("!sonarr calendar 3"))
        core._command_cooldown._last.clear()
        process(message("!sonarr calendar 99", "m2"))
    assert "2026-10-01 Show S01E04" in client.sent[0]["content"]
    assert "at most" in client.sent[-1]["content"]


def test_plaintext_is_allowed_only_to_loopback_or_a_bare_service_name():
    saved = core.SONARR_URL
    try:
        for url, ok in (("http://sonarr:8989", True), ("http://127.0.0.1:8989", True), ("http://sonarr.example.com", False), ("https://sonarr.example.com", True)):
            core.SONARR_URL = url
            assert (core.check_sonarr_config() is None) is ok, url
    finally:
        core.SONARR_URL = saved


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
