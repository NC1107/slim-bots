#!/usr/bin/env python3
"""Radarr's history mapping, post wording, live poll and command wording with its api faked; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("RADARR_URL", "http://radarr:7878")
os.environ.setdefault("RADARR_API_KEY", "fake-key")

import bot as radarr  # noqa: E402
import radarr_cog  # noqa: E402
import radarr_core as core  # noqa: E402
from arrkit import history, library, poller, store  # noqa: E402
from arrkit.testkit import FakeApi, Harness, Patched, labels, newest_buttons  # noqa: E402

harness = Harness(radarr.bot, core.init_db)


def record(rid, kind, tmdb=100, title="Movie", year=2020, quality="WEBDL-1080p", data=None, source="Movie.2020"):
    return {
        "id": rid, "eventType": kind, "movieId": tmdb, "sourceTitle": source, "quality": {"quality": {"name": quality}},
        "data": data or {}, "movie": {"title": title, "year": year, "tmdbId": tmdb, "imdbId": f"tt{tmdb}"},
    }


def announce(conn, records):
    """Plans, then records the marks the way a landed post does."""
    posts = core.build_posts(core.plan_events(conn, records))
    for post in posts:
        store.mark_announced(conn, post["marks"])
    return [history.render_text(p) for p in posts]


def fresh_db():
    conn = sqlite3.connect(":memory:")
    core.init_db(conn)
    return conn


def test_an_import_reads_as_downloaded_with_title_year_and_quality():
    assert announce(fresh_db(), [record(1, "downloadFolderImported")]) == ["Downloaded: Movie (2020) [WEBDL-1080p]"]


def test_a_bulk_import_is_one_summary_and_a_few_stay_separate_posts():
    burst = [record(i, "downloadFolderImported", tmdb=i, title=f"M{i}") for i in range(1, 9)]
    assert announce(fresh_db(), burst) == ["Downloaded: 8 movies (M1 (2020), M2 (2020), M3 (2020), M4 (2020), M5 (2020), ...)"]
    few = [record(i, "downloadFolderImported", tmdb=i, title=f"M{i}") for i in (1, 2)]
    assert len(announce(fresh_db(), few)) == 2


def test_a_replaced_file_does_not_repost_and_a_better_one_is_a_single_upgrade():
    conn = fresh_db()
    assert len(announce(conn, [record(1, "downloadFolderImported")])) == 1
    assert announce(conn, [record(2, "downloadFolderImported")]) == []
    assert announce(conn, [record(3, "downloadFolderImported", quality="Bluray-1080p")]) == ["Upgraded: Movie (2020) [Bluray-1080p]"]
    assert announce(conn, [record(4, "downloadFolderImported", quality="Bluray-1080p")]) == []


def test_the_same_movie_under_a_new_radarr_id_or_without_a_tmdb_id_is_still_the_same_movie():
    conn = fresh_db()
    announce(conn, [record(1, "downloadFolderImported")])
    readded = record(2, "downloadFolderImported")
    readded["movieId"] = 999
    assert announce(conn, [readded]) == []
    imdb_only = record(3, "downloadFolderImported", tmdb=0, title="Other")
    imdb_only["movie"]["tmdbId"] = None
    assert core._ident(imdb_only) == "imdb:tt0"


def test_an_upgrade_delete_of_the_movie_in_the_batch_makes_the_import_an_upgrade_but_a_manual_delete_does_not():
    upgrade = [record(1, "movieFileDeleted", data={"reason": "Upgrade"}), record(2, "downloadFolderImported", quality="Bluray-1080p")]
    assert announce(fresh_db(), upgrade)[0].startswith("Upgraded:")
    manual = [record(1, "movieFileDeleted", data={"reason": "Manual"}), record(2, "downloadFolderImported")]
    assert announce(fresh_db(), manual)[0].startswith("Downloaded:")


def test_a_grab_names_its_indexer_and_a_failure_names_why():
    conn = fresh_db()
    assert announce(conn, [record(1, "grabbed", data={"indexer": "NZBgeek"})]) == ["Grabbed: Movie (2020) [WEBDL-1080p] from NZBgeek"]
    failed = record(2, "downloadFailed", data={"message": "Aborted, cannot be completed"}, source="Movie.x")
    assert announce(conn, [failed]) == ["Failed: Movie (2020) - Aborted, cannot be completed"]


def test_a_record_missing_its_movie_is_skipped():
    broken = record(1, "grabbed")
    broken["movie"] = None
    assert core.plan_events(fresh_db(), [broken]) == []


def history_api(records):
    def page(params):
        size = params.get("pageSize", 100)
        return {"records": sorted(records, key=lambda r: -r["id"])[(params.get("page", 1) - 1) * size:][:size]}

    return FakeApi(**{"/history": page})


def test_the_live_poll_bootstraps_then_posts_a_new_import_once_as_an_embed_in_the_channel():
    client = harness.setup()
    records = [record(7, "downloadFolderImported", tmdb=7)]
    with Patched(core, "api", history_api(records)):
        asyncio.run(radarr.poll_once())
        assert client.sent == []
        records.append(record(8, "downloadFolderImported", tmdb=8, title="Second"))
        asyncio.run(radarr.poll_once())
        asyncio.run(radarr.poll_once())
    assert len(client.sent) == 1 and client.sent[0]["channel_id"] == "c1"
    assert client.sent[0]["embeds"][0]["title"] == "Downloaded: Second (2020) [WEBDL-1080p]"


def test_the_poll_asks_radarr_for_the_movie_inline():
    client = harness.setup()
    api = history_api([record(1, "grabbed")])
    with Patched(core, "api", api):
        asyncio.run(poller.history_poll_once(radarr.bot, core))
        asyncio.run(poller.history_poll_once(radarr.bot, core))
    assert all(c[2].get("includeMovie") == "true" for c in api.calls[1:]) and client.sent == []


def command(text, **routes):
    client = harness.setup()
    radarr_cog.GUARD.cooldown._last.clear()
    radarr_cog.CHOOSER._picks.clear()
    api = FakeApi(**routes)
    with Patched(core, "api", api):
        harness.process(harness.message(text))
    return client, api


def test_queue_names_the_movie_and_its_time_left():
    queue = {"records": [{"title": "x", "status": "downloading", "timeleft": "00:10:00", "movie": {"title": "Dune", "year": 2021}}], "totalRecords": 1}
    client, _ = command("!radarr queue", **{"/queue": queue})
    assert "1 in the queue:\n- Dune (2021) - downloading, 00:10:00 left" in client.sent[-1]["content"]


def test_calendar_dates_a_movie_by_the_release_inside_the_window():
    today = library.calendar_window(1)[0]
    items = [{"title": "Dune", "year": 2021, "inCinemas": "2000-01-01T00:00:00Z", "digitalRelease": f"{today}T00:00:00Z", "hasFile": False}]
    client, _ = command("!radarr calendar 3", **{"/calendar": items})
    assert f"{today} Dune (2021)" in client.sent[-1]["content"]


def test_a_release_date_outside_the_window_falls_back_to_the_earliest_known():
    assert radarr_cog.release_in_window({"inCinemas": "2030-05-01T00:00:00Z", "physicalRelease": "2030-07-01T00:00:00Z"}, "2026-01-01", "2026-01-08") == "2030-05-01"
    assert radarr_cog.release_in_window({}, "2026-01-01", "2026-01-08") == ""


def test_adding_a_movie_posts_it_monitored_and_searches():
    lookup = [{"title": "Dune", "year": 2021, "tmdbId": 1, "titleSlug": "dune-1"}]
    client, api = command("!radarr add dune", **{"/movie/lookup": lookup, "/qualityprofile": [{"id": 4, "name": "HD-1080p"}], "/rootfolder": [{"path": "/media/movies"}]})
    assert labels(newest_buttons(client)) == ["Dune (2021)", "Cancel"]
    with Patched(core, "api", api):
        harness.press_chooser(client, ("radarrpick:sel:0", "u1"))
    _method, path, _params, body = api.posts()[0]
    assert path == "/movie" and body["tmdbId"] == 1 and body["qualityProfileId"] == 4 and body["rootFolderPath"] == "/media/movies"
    assert body["monitored"] and body["addOptions"] == {"searchForMovie": True}
    assert "added **Dune (2021)** - searching for a release now." in client.edited[-1]["content"]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
