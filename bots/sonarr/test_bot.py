#!/usr/bin/env python3
"""Sonarr's history mapping, post wording, live poll and command wording with its api faked; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SONARR_URL", "http://sonarr:8989")
os.environ.setdefault("SONARR_API_KEY", "fake-key")

import bot as sonarr  # noqa: E402
import sonarr_cog  # noqa: E402
import sonarr_core as core  # noqa: E402
from arrkit import history, poller, store  # noqa: E402
from arrkit.testkit import FakeApi, Harness, Patched, labels, newest_buttons  # noqa: E402

harness = Harness(sonarr.bot, core.init_db)


def record(rid, kind, episode=1, season=1, series_id=1, quality="WEBDL-1080p", data=None, source="Show.S01E01"):
    return {
        "id": rid, "eventType": kind, "seriesId": series_id, "episodeId": series_id * 1000 + season * 100 + episode,
        "sourceTitle": source, "quality": {"quality": {"name": quality}}, "data": data or {},
        "series": {"title": "Show", "year": 2020}, "episode": {"seasonNumber": season, "episodeNumber": episode, "title": f"Ep {episode}"},
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


def test_an_import_reads_as_downloaded_with_series_year_episode_and_quality():
    assert announce(fresh_db(), [record(1, "downloadFolderImported")]) == ["Downloaded: Show (2020) S01E01 - Ep 1 [WEBDL-1080p]"]


def test_a_season_pack_is_one_post_not_one_per_episode():
    pack = [record(i, "downloadFolderImported", episode=i) for i in range(1, 9)]
    assert announce(fresh_db(), pack) == ["Downloaded: Show (2020) S01, 8 episodes (E01-E08) [WEBDL-1080p]"]


def test_a_gappy_pack_lists_its_episodes_and_two_seasons_stay_two_posts():
    gappy = [record(1, "downloadFolderImported", episode=1), record(2, "downloadFolderImported", episode=4)]
    assert announce(fresh_db(), gappy) == ["Downloaded: Show (2020) S01, 2 episodes (E01, E04) [WEBDL-1080p]"]
    two = [record(3, "downloadFolderImported", season=1), record(4, "downloadFolderImported", season=2)]
    assert len(announce(fresh_db(), two)) == 2


def test_a_replaced_file_does_not_repost_and_a_better_one_is_a_single_upgrade():
    conn = fresh_db()
    assert len(announce(conn, [record(1, "downloadFolderImported")])) == 1
    assert announce(conn, [record(2, "downloadFolderImported")]) == []
    assert announce(conn, [record(3, "downloadFolderImported", quality="Bluray-1080p")]) == ["Upgraded: Show (2020) S01E01 - Ep 1 [Bluray-1080p]"]
    assert announce(conn, [record(4, "downloadFolderImported", quality="Bluray-1080p")]) == []


def test_an_upgrade_delete_of_the_episode_in_the_batch_makes_the_import_an_upgrade():
    batch = [record(1, "episodeFileDeleted", data={"reason": "Upgrade"}), record(2, "downloadFolderImported", quality="Bluray-1080p")]
    assert announce(fresh_db(), batch)[0].startswith("Upgraded:")
    other = [record(1, "episodeFileDeleted", data={"reason": "Upgrade"}, episode=2), record(2, "downloadFolderImported")]
    assert announce(fresh_db(), other)[0].startswith("Downloaded:")


def test_a_grab_names_its_indexer_and_a_failure_names_why():
    conn = fresh_db()
    assert announce(conn, [record(1, "grabbed", data={"indexer": "NZBgeek"})]) == ["Grabbed: Show (2020) S01E01 - Ep 1 [WEBDL-1080p] from NZBgeek"]
    failed = record(2, "downloadFailed", data={"message": "Aborted, cannot be completed"}, source="Show.S01E01.x")
    assert announce(conn, [failed]) == ["Failed: Show (2020) S01E01 - Ep 1 - Aborted, cannot be completed"]


def test_records_missing_their_series_or_episode_are_skipped():
    for field in ("series", "episode"):
        broken = record(1, "grabbed")
        broken[field] = None
        assert core.plan_events(fresh_db(), [broken]) == []


def history_api(records):
    def page(params):
        size = params.get("pageSize", 100)
        return {"records": sorted(records, key=lambda r: -r["id"])[(params.get("page", 1) - 1) * size:][:size]}

    return FakeApi(**{"/history": page})


def test_the_live_poll_bootstraps_then_posts_a_new_import_once_as_an_embed_in_the_channel():
    client = harness.setup()
    records = [record(7, "downloadFolderImported")]
    with Patched(core, "api", history_api(records)):
        asyncio.run(sonarr.poll_once())
        assert client.sent == []
        records.append(record(8, "downloadFolderImported", episode=2))
        asyncio.run(sonarr.poll_once())
        asyncio.run(sonarr.poll_once())
    assert len(client.sent) == 1 and client.sent[0]["channel_id"] == "c1"
    assert client.sent[0]["embeds"][0]["title"].startswith("Downloaded: Show (2020) S01E02")
    assert client.sent[0]["content"] == ""


def test_the_poll_asks_sonarr_for_the_series_and_episode_inline():
    client = harness.setup()
    api = history_api([record(1, "grabbed")])
    with Patched(core, "api", api):
        asyncio.run(poller.history_poll_once(sonarr.bot, core))
        asyncio.run(poller.history_poll_once(sonarr.bot, core))
    assert all(c[2].get("includeSeries") == "true" for c in api.calls[1:])
    assert client.sent == []


def command(text, **routes):
    client = harness.setup()
    sonarr_cog.GUARD.cooldown._last.clear()
    sonarr_cog.CHOOSER._picks.clear()
    api = FakeApi(**routes)
    with Patched(core, "api", api):
        harness.process(harness.message(text))
    return client, api


def test_queue_names_the_show_and_episode_and_its_time_left():
    queue = {"records": [{"title": "x", "status": "downloading", "timeleft": "00:10:00", "series": {"title": "Show"}, "episode": {"seasonNumber": 2, "episodeNumber": 3}}], "totalRecords": 1}
    client, _ = command("!sonarr queue", **{"/queue": queue})
    assert "1 in the queue:\n- Show S02E03 - downloading, 00:10:00 left" in client.sent[-1]["content"]


def test_calendar_lists_air_dates_and_marks_what_is_downloaded():
    items = [{"airDateUtc": "2026-10-01T01:00:00Z", "seasonNumber": 1, "episodeNumber": 4, "series": {"title": "Show"}, "hasFile": True}]
    client, api = command("!sonarr calendar 3", **{"/calendar": items})
    assert "2026-10-01 Show S01E04 - downloaded" in client.sent[-1]["content"]
    assert api.calls[0][2]["includeSeries"] == "true"


def test_adding_a_show_posts_it_monitored_with_the_first_profile_and_folder_and_searches():
    lookup = [{"title": "Severance", "year": 2022, "tvdbId": 1, "titleSlug": "severance", "seasons": [{"seasonNumber": 1}]}]
    client, api = command("!sonarr add severance", **{"/series/lookup": lookup, "/qualityprofile": [{"id": 4, "name": "HD-1080p"}], "/rootfolder": [{"path": "/media/tv"}]})
    assert labels(newest_buttons(client)) == ["Severance (2022)", "Cancel"]
    with Patched(core, "api", api):
        harness.press_chooser(client, ("sonarrpick:sel:0", "u1"))
    _method, path, _params, body = api.posts()[0]
    assert path == "/series" and body["tvdbId"] == 1 and body["qualityProfileId"] == 4 and body["rootFolderPath"] == "/media/tv"
    assert body["monitored"] and body["addOptions"] == {"monitor": "all", "searchForMissingEpisodes": True}
    assert "added **Severance (2022)** - searching for episodes now." in client.edited[-1]["content"]


def test_a_configured_profile_name_and_root_folder_win_over_the_first_listed():
    saved = core.QUALITY_PROFILE, core.ROOT_FOLDER
    core.QUALITY_PROFILE, core.ROOT_FOLDER = "ultra", "/media/other"
    try:
        api = FakeApi(**{"/qualityprofile": [{"id": 1, "name": "SD"}, {"id": 9, "name": "Ultra"}], "/rootfolder": [{"path": "/media/tv"}]})
        with Patched(core, "api", api):
            core.add({"title": "X", "tvdbId": 5})
    finally:
        core.QUALITY_PROFILE, core.ROOT_FOLDER = saved
    assert api.posts()[0][3]["qualityProfileId"] == 9 and api.posts()[0][3]["rootFolderPath"] == "/media/other"


def test_adding_with_no_profile_or_root_folder_says_what_sonarr_lacks():
    client, api = command("!sonarr add x", **{"/series/lookup": [{"title": "X", "tvdbId": 1}], "/qualityprofile": [], "/rootfolder": []})
    with Patched(core, "api", api):
        harness.press_chooser(client, ("sonarrpick:sel:0", "u1"))
    assert "sonarr has no quality profile or root folder to add into" in client.edited[-1]["content"]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
