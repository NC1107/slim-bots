#!/usr/bin/env python3
"""Command-and-event tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as modlog  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": [], "roles": []}]


def message(author_id, content, msg_id="m1"):
    return {"id": msg_id, "author_id": author_id, "channel_id": "c1", "content": content}


def setup():
    modlog.bot.db = sqlite3.connect(":memory:")
    modlog.init_db(modlog.bot.db)
    modlog.bot.channels = {"c1"}
    modlog._last_roles.clear()
    modlog._role_names.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    modlog.bot.client = client
    modlog.bot.space = Space(client)
    modlog.bot.authors = AuthorFilter(client, space=modlog.bot.space, ignore_bots=True)
    modlog.bot.me_id = "bot-1"
    asyncio.run(modlog.bot.space.refresh_members())
    return client


def dispatch_frame(frame):
    asyncio.run(modlog.bot._handle_frame(frame))


def process(client, *messages):
    async def run():
        for msg in messages:
            await modlog.bot.process_message(msg)

    asyncio.run(run())


def test_member_timeout_is_logged():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.timeout", "user_id": "u1", "until": 9999999999000})
    assert "was timed out" in client.sent[-1]["content"]


def test_member_timeout_lifted_is_logged():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.timeout", "user_id": "u1", "until": None})
    assert "timeout was lifted" in client.sent[-1]["content"]


def test_member_removed_is_logged():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.removed", "user_id": "u1"})
    assert "was removed from the Space" in client.sent[-1]["content"]
    assert client.sent[-1]["embeds"] == [{"footer": {"text": "member.removed"}}]


def test_role_change_first_sighting_reads_now_holds():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": ["r1"], "roles": ["helper"]})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1"})
    assert "now holds" in client.sent[-1]["content"]
    assert "helper" in client.sent[-1]["content"]


def test_role_change_grant_then_revoke():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": [], "roles": []})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1"})
    assert "does not hold" in client.sent[-1]["content"]

    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": ["r1"], "roles": ["helper"]})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1"})
    assert "was granted" in client.sent[-1]["content"]

    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": [], "roles": []})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1"})
    assert "was revoked from" in client.sent[-1]["content"]


def test_role_definition_change_unresolvable_name():
    client = setup()
    dispatch_frame({"type": "role.changed", "role_id": "r-unknown"})
    assert "cannot be resolved here" in client.sent[-1]["content"]


def test_modlog_stats_reports_nothing_recorded_yet():
    client = setup()
    process(client, message("u1", "!modlog stats"))
    assert client.sent[-1]["content"] == "nothing recorded yet."


def test_modlog_stats_counts_logged_events():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.removed", "user_id": "u1"})
    process(client, message("u1", "!modlog stats"))
    assert "member.removed: 1" in client.sent[-1]["content"]


def test_modlog_gaps_reports_none_recorded():
    client = setup()
    process(client, message("u1", "!modlog gaps"))
    assert client.sent[-1]["content"] == "no reconnect gaps recorded."


def test_modlog_permissions_names_both_gaps():
    client = setup()
    process(client, message("u1", "!modlog permissions"))
    assert "MANAGE_MESSAGES" in client.sent[-1]["content"]
    assert "MANAGE_ROLES" in client.sent[-1]["content"]


def test_modlog_bad_subcommand_is_a_clear_reply():
    client = setup()
    process(client, message("u1", "!modlog nonsense"))
    assert "try `!modlog" in client.sent[-1]["content"]


def test_reconnect_gap_is_recorded_and_reported():
    setup()
    modlog._last_seen_at = None
    asyncio.run(modlog.report_reconnect_gap())
    assert modlog.bot.db.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 0

    modlog.note_alive()
    modlog._last_seen_at -= 100
    client = modlog.bot.client
    asyncio.run(modlog.report_reconnect_gap())
    assert "reconnected after" in client.sent[-1]["content"]
    assert modlog.bot.db.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 1


def test_a_short_gap_is_not_reported():
    setup()
    modlog.note_alive()
    client = modlog.bot.client
    asyncio.run(modlog.report_reconnect_gap())
    assert client.sent == []


def test_another_bot_is_ignored_by_default():
    client = setup()
    client.respond(
        "GET",
        "/members",
        MEMBERS + [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": [], "roles": []}],
    )
    asyncio.run(modlog.bot.space.refresh_members())
    process(client, message("bot-2", "!modlog stats"))
    assert client.sent == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
