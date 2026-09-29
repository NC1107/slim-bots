#!/usr/bin/env python3
"""The `!radarr add` button chooser: who may press, expiry, and what reaches Radarr; run directly: python3 test_picker.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import picker  # noqa: E402
import radarr_core as core  # noqa: E402
from test_bot import FakeRadarr, Patched, message, process, setup, radarr  # noqa: E402

SHOWS = [
    {"title": "Dune", "year": 2021, "tmdbId": 1, "titleSlug": "dune-1"},
    {"title": "Dune Old", "year": 1984, "tmdbId": 2, "id": 7},
    {"title": "Dune Part Two", "year": 2024, "tmdbId": 3},
]


def press(custom_id, message_id, user_id="u1"):
    return {
        "type": "interaction.created", "interaction_id": f"i-{custom_id}", "channel_id": "c1", "message_id": message_id,
        "custom_id": custom_id, "user_id": user_id, "user_display_name": user_id, "created_at": 1,
    }


def chooser(client):
    return next(m for m in reversed(client.sent) if m.get("components"))


def labels(sent):
    return [b["label"] for row in sent["components"] for b in row["buttons"]]


async def settle():
    while True:
        pending = [t for t in radarr.bot._background_tasks if not t.get_name().startswith("radarr-pick-")]
        if not pending:
            return
        await asyncio.gather(*pending)


def run_add(fake, *presses):
    """Runs `!radarr add dune`, then each `(custom_id, user_id)` press on the chooser."""
    client = setup()
    picker._picks.clear()

    async def flow():
        await radarr.bot.process_message(message("!radarr add dune"))
        for custom_id, user_id in presses:
            await radarr.bot._handle_frame(press(custom_id, chooser(client)["id"], user_id))
            await settle()

    with Patched(fake):
        asyncio.run(flow())
    return client


def test_the_chooser_offers_only_shows_not_already_in_the_library():
    fake = FakeRadarr()
    fake.lookup = SHOWS
    client = run_add(fake)
    assert labels(chooser(client)) == ["Dune (2021)", "Dune Part Two (2024)", "Cancel"]


def test_when_everything_is_already_in_the_library_no_chooser_opens():
    fake = FakeRadarr()
    fake.lookup = [SHOWS[1]]
    client = run_add(fake)
    assert "already in the library" in client.sent[-1]["content"] and not client.sent[-1].get("components")


def test_choosing_adds_the_movie_monitored_with_the_first_profile_and_folder():
    fake = FakeRadarr()
    fake.lookup = SHOWS
    client = run_add(fake, (f"{picker.ID_PREFIX}sel:0", "u1"))
    method, path, _params, body = next(c for c in fake.calls if c[0] == "POST")
    assert (method, path) == ("POST", "/movie")
    assert body["tmdbId"] == 1 and body["qualityProfileId"] == 4 and body["rootFolderPath"] == "/media/movies" and body["monitored"]
    assert body["addOptions"]["searchForMovie"] is True
    assert "added **Dune (2021)**" in client.edited[-1]["content"]
    assert client.component_edits[-1]["components"] == []


def test_another_member_pressing_is_told_no_and_nothing_is_added():
    fake = FakeRadarr()
    fake.lookup = SHOWS
    client = run_add(fake, (f"{picker.ID_PREFIX}sel:0", "u2"))
    assert not any(c[0] == "POST" for c in fake.calls)
    assert "only Nick can choose" in client.ephemerals[-1]["content"]


def test_cancel_closes_the_chooser_without_adding():
    fake = FakeRadarr()
    fake.lookup = SHOWS
    client = run_add(fake, (f"{picker.ID_PREFIX}cancel", "u1"))
    assert client.edited[-1]["content"] == "cancelled." and not any(c[0] == "POST" for c in fake.calls)


def test_a_second_press_after_choosing_cannot_add_twice():
    fake = FakeRadarr()
    fake.lookup = SHOWS
    client = setup()
    picker._picks.clear()

    async def flow():
        await radarr.bot.process_message(message("!radarr add dune"))
        message_id = chooser(client)["id"]
        for _ in range(2):
            await radarr.bot._handle_frame(press(f"{picker.ID_PREFIX}sel:0", message_id))
            await settle()

    with Patched(fake):
        asyncio.run(flow())
    assert len([c for c in fake.calls if c[0] == "POST"]) == 1
    assert "expired" in client.ephemerals[-1]["content"]


def test_an_expired_chooser_refuses_and_closes():
    fake = FakeRadarr()
    fake.lookup = SHOWS
    client = setup()
    picker._picks.clear()

    async def flow():
        await radarr.bot.process_message(message("!radarr add dune"))
        next(iter(picker._picks.values())).created_at -= picker.EXPIRES_SECONDS + 1
        await radarr.bot._handle_frame(press(f"{picker.ID_PREFIX}sel:0", chooser(client)["id"]))
        await settle()

    with Patched(fake):
        asyncio.run(flow())
    assert not any(c[0] == "POST" for c in fake.calls)
    assert "timed out" in client.edited[-1]["content"]


def test_radarr_refusing_the_add_is_reported_in_the_chooser():
    fake = FakeRadarr()
    fake.lookup = SHOWS
    fake.refuse_posts = RuntimeError("radarr said no")
    client = run_add(fake, (f"{picker.ID_PREFIX}sel:0", "u1"))
    assert "could not add **Dune (2021)**: radarr said no." in client.edited[-1]["content"]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
