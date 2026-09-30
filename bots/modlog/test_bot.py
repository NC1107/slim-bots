#!/usr/bin/env python3
"""Command-and-event tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as modlog  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": [], "roles": []}]


def message(author_id, content, msg_id="m1"):
    return {"id": msg_id, "author_id": author_id, "channel_id": "c1", "content": content}


def setup():
    modlog.bot.store = Store(":memory:", migrate=modlog.init_db)
    asyncio.run(modlog.bot.store.open())
    modlog.bot.channels = {"c1"}
    modlog._last_roles.clear()
    modlog._role_names.clear()
    modlog._open_gap_notice = None
    modlog.bot.moderation_head = None
    modlog.MODERATION_SETTLE_SECONDS = 0
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


class prefixed:
    """Runs a block with the bot answering to `?`, the way `SLIMM_PREFIX` would set it."""

    def __enter__(self):
        self.original, modlog.bot.prefix = modlog.bot.prefix, "?"

    def __exit__(self, *_exc):
        modlog.bot.prefix = self.original


def test_modlog_hints_name_the_configured_prefix():
    client = setup()
    with prefixed():
        process(client, message("u1", "?modlog nonsense"))
        assert "try `?modlog stats`" in client.sent[-1]["content"]
        assert "`?modlog permissions`" in modlog.gap_notice_text(1, 3600)


def test_modlog_bad_subcommand_is_a_clear_reply():
    client = setup()
    process(client, message("u1", "!modlog nonsense"))
    assert "try `!modlog" in client.sent[-1]["content"]


def test_reconnect_gap_is_recorded_and_reported():
    setup()
    modlog._last_seen_at = None
    asyncio.run(modlog.report_reconnect_gap())
    assert modlog.bot.store.connection.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 0

    modlog.note_alive()
    modlog._last_seen_at -= 4000
    client = modlog.bot.client
    asyncio.run(modlog.report_reconnect_gap())
    assert "reconnected after approximately 1h6m offline" in client.sent[-1]["content"]
    assert "embeds" not in client.sent[-1]
    assert modlog.bot.store.connection.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 1


def test_a_blip_is_recorded_but_not_posted():
    setup()
    modlog.note_alive()
    modlog._last_seen_at -= 60
    client = modlog.bot.client
    asyncio.run(modlog.report_reconnect_gap())
    assert client.sent == []
    assert modlog.bot.store.connection.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 1


def reconnect_after(seconds):
    modlog.note_alive()
    modlog._last_seen_at -= seconds
    asyncio.run(modlog.report_reconnect_gap())


def test_back_to_back_gaps_edit_one_notice():
    client = setup()
    reconnect_after(600)
    first_id = client.sent[-1]["id"]
    client.respond("PATCH", f"/channels/{modlog.bot.channel}/messages/{first_id}", {})
    reconnect_after(900)
    assert len(client.sent) == 1
    method, path, body, _ = client.calls[-1]
    assert (method, path) == ("PATCH", f"/channels/{modlog.bot.channel}/messages/{first_id}")
    assert "reconnected 2 times, approximately 25m offline in total" in body["content"]


def test_a_gap_after_a_log_line_gets_a_fresh_notice():
    client = setup()
    reconnect_after(600)
    asyncio.run(modlog.post("member.role_changed", "Nick was granted x"))
    reconnect_after(600)
    assert len(client.sent) == 3


def test_a_short_gap_is_not_reported():
    setup()
    modlog.note_alive()
    client = modlog.bot.client
    asyncio.run(modlog.report_reconnect_gap())
    assert client.sent == []


def stored_moderation_seq():
    return modlog.bot.store.connection.execute("SELECT last_seq FROM moderation_state").fetchone()


def stored_build():
    return modlog.bot.store.connection.execute("SELECT build FROM moderation_state").fetchone()[0]


def restart_gap_count():
    return modlog.bot.store.connection.execute("SELECT COUNT(*) FROM moderation_gaps WHERE after_restart = 1").fetchone()[0]


def moderation_gap_count():
    return modlog.bot.store.connection.execute("SELECT COUNT(*) FROM moderation_gaps").fetchone()[0]


def serve_version(version, capabilities=("push",)):
    modlog.bot.client.respond("GET", "/version", {"version": version, "capabilities": list(capabilities)})


def ready_with_head(head, version="0.76.0"):
    serve_version(version)
    modlog.bot.moderation_head = head

    async def run():
        await modlog.check_moderation_cursor()
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())


def ready_in_one_loop(*connects):
    async def run():
        for head, version in connects:
            serve_version(version)
            modlog.bot.moderation_head = head
            await modlog.check_moderation_cursor()
            while modlog.bot._background_tasks:
                await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())


def test_the_highest_moderation_seq_is_persisted():
    setup()
    for seq in (30, 10, 20):
        dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": seq})
    assert stored_moderation_seq() == (30,)


def test_a_frame_without_a_moderation_seq_is_not_persisted():
    setup()
    dispatch_frame({"type": "message.created", "seq": 99, "message": {}})
    assert stored_moderation_seq() is None


def test_the_first_connect_seeds_the_cursor_without_a_marker():
    setup()
    ready_with_head(500)
    assert stored_moderation_seq() == (500,)
    assert moderation_gap_count() == 0
    assert stored_build() == "0.76.0+push"


def test_a_head_ahead_on_an_unchanged_build_logs_a_plain_gap_marker():
    setup()
    ready_with_head(50)
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    ready_with_head(200)
    assert moderation_gap_count() == 1
    assert restart_gap_count() == 0
    assert stored_moderation_seq() == (200,)
    kinds = modlog.bot.store.connection.execute("SELECT kind, text FROM events WHERE kind = 'gap'").fetchall()
    assert len(kinds) == 1 and "unchanged server build" in kinds[0][1]


def test_a_head_ahead_after_the_build_changed_is_a_restart_gap():
    setup()
    ready_with_head(50, version="0.76.0")
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    ready_with_head(200, version="0.77.0")
    assert moderation_gap_count() == 1
    assert restart_gap_count() == 1
    text = modlog.bot.store.connection.execute("SELECT text FROM events WHERE kind = 'gap'").fetchone()[0]
    assert text == "server restarted, events may have been missed"
    assert stored_build() == "0.77.0+push"


def test_a_changed_capability_list_counts_as_a_changed_build():
    setup()
    ready_with_head(50)
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    serve_version("0.76.0", capabilities=("push", "polls"))
    modlog.bot.moderation_head = 200

    async def run():
        await modlog.check_moderation_cursor()
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())
    assert restart_gap_count() == 1


def test_an_unreadable_version_keeps_the_gap_plain():
    setup()
    ready_with_head(50)
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    modlog.bot.client.respond("GET", "/version", {})
    modlog.bot.moderation_head = 200

    async def run():
        await modlog.check_moderation_cursor()
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())
    assert moderation_gap_count() == 1
    assert restart_gap_count() == 0
    assert stored_build() == "0.76.0+push"


def test_a_head_at_or_below_the_last_seq_is_not_a_gap():
    setup()
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    ready_with_head(100)
    assert moderation_gap_count() == 0


def test_an_event_landing_after_the_hello_closes_the_gap():
    setup()
    serve_version("0.76.0")
    modlog.MODERATION_SETTLE_SECONDS = 0.2
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    modlog.bot.moderation_head = 200

    async def run():
        await modlog.check_moderation_cursor()
        await modlog.note_moderation_seq({"type": "role.changed", "seq": 200})
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())
    assert moderation_gap_count() == 0


def test_a_server_without_a_moderation_head_changes_nothing():
    setup()
    ready_with_head(None)
    assert stored_moderation_seq() is None


def test_modlog_gaps_mentions_a_cursor_gap_with_no_reconnect_gap():
    client = setup()
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    ready_in_one_loop((200, "0.76.0"), (300, "0.77.0"))
    process(client, message("u1", "!modlog gaps"))
    reply = client.sent[-1]["content"]
    assert "ahead of this log 1 time(s) on an unchanged server build" in reply
    assert "1 more time(s) it was ahead after the server restarted" in reply


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
