#!/usr/bin/env python3
"""Unit tests for bot.py's event handling and commands, using
slimbots.testing.FakeClient so none of this needs a live deployment or a
socket.

Run it directly, no test framework needed:

    pip install -r requirements.txt
    python3 test_bot.py
"""

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402
from slimbots.testing import FakeClient  # noqa: E402

ME = "bot-1"
USER = "user-1"
OTHER_BOT = "bot-2"
bot.LOG_CHANNEL = "chan-1"


def fresh():
    bot._names.clear()
    bot._last_roles.clear()
    bot._role_names.clear()
    bot._is_bot_cache.clear()
    bot._last_seen_at = None
    conn = sqlite3.connect(":memory:")
    bot.init_db(conn)
    client = FakeClient(me_id=ME)
    client.respond("GET", f"/users/{USER}", {"display_name": "nick", "role_ids": [], "roles": [], "is_bot": False})
    client.respond("GET", f"/users/{OTHER_BOT}", {"is_bot": True, "role_ids": []})
    return client, conn


def event_counts(conn):
    return dict(conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall())


def test_format_duration_buckets():
    assert bot.format_duration(45) == "45s"
    assert bot.format_duration(150) == "2m"
    assert bot.format_duration(3660) == "1h1m"
    assert bot.format_duration(3600) == "1h"
    assert bot.format_duration(90000) == "1d1h"


def test_timeout_with_duration_is_formatted():
    client, conn = fresh()
    until = int((time.time() + 7200) * 1000)
    bot.handle_frame(client, conn, {"type": "member.timeout", "user_id": USER, "until": until})
    text = client.sent[0]["content"]
    assert "nick was timed out" in text
    assert "from now" in text
    assert event_counts(conn) == {"member.timeout": 1}


def test_timeout_lifted_has_no_duration_text():
    client, conn = fresh()
    bot.handle_frame(client, conn, {"type": "member.timeout", "user_id": USER, "until": None})
    assert client.sent[0]["content"] == "nick's timeout was lifted"


def test_role_grant_is_inferred_from_a_diff():
    client, conn = fresh()
    client.respond(
        "GET", f"/users/{USER}", {"display_name": "nick", "role_ids": ["r1"], "roles": ["helper"], "is_bot": False}
    )
    bot._last_roles[USER] = set()
    bot.handle_frame(client, conn, {"type": "member.role_changed", "user_id": USER, "role_id": "r1"})
    assert "was granted helper" in client.sent[0]["content"]


def test_role_definition_change_with_unknown_name():
    client, conn = fresh()
    bot.handle_frame(client, conn, {"type": "role.changed", "role_id": "r-unknown"})
    assert "cannot be resolved" in client.sent[0]["content"]


def test_handle_command_ignores_self_and_other_bots():
    client, conn = fresh()
    bot.handle_command(client, conn, ME, {"author_id": ME, "content": "!modlog stats", "id": "m1"})
    bot.handle_command(client, conn, ME, {"author_id": OTHER_BOT, "content": "!modlog stats", "id": "m2"})
    assert client.sent == []


def test_stats_reports_nothing_when_empty():
    client, conn = fresh()
    bot.handle_command(client, conn, ME, {"author_id": USER, "content": "!modlog stats", "id": "m1"})
    assert client.sent[0]["content"] == "nothing recorded yet."


def test_stats_counts_by_kind():
    client, conn = fresh()
    bot.record_event(conn, "member.removed", "x was removed from the Space")
    bot.record_event(conn, "member.removed", "y was removed from the Space")
    bot.record_event(conn, "member.restored", "x was let back into the Space")
    bot.handle_command(client, conn, ME, {"author_id": USER, "content": "!modlog stats", "id": "m1"})
    reply = client.sent[0]["content"]
    assert "member.removed: 2" in reply
    assert "member.restored: 1" in reply


def test_gaps_reports_none_when_empty():
    client, conn = fresh()
    bot.handle_command(client, conn, ME, {"author_id": USER, "content": "!modlog gaps", "id": "m1"})
    assert client.sent[0]["content"] == "no reconnect gaps recorded."


def test_permissions_names_both_gaps():
    client, conn = fresh()
    bot.handle_command(client, conn, ME, {"author_id": USER, "content": "!modlog permissions", "id": "m1"})
    reply = client.sent[0]["content"]
    assert "MANAGE_MESSAGES" in reply
    assert "MANAGE_ROLES" in reply


def test_first_connect_reports_no_gap():
    client, conn = fresh()
    bot.report_reconnect_gap(client, conn)
    assert client.sent == []


def test_reconnect_after_a_real_gap_posts_and_records_it():
    client, conn = fresh()
    bot._last_seen_at = time.time() - 30
    bot.report_reconnect_gap(client, conn)
    assert "approximately" in client.sent[0]["content"]
    rows = conn.execute("SELECT downtime_seconds FROM gaps").fetchall()
    assert len(rows) == 1
    assert rows[0][0] >= 30


def test_a_short_reconnect_is_not_worth_a_post():
    client, conn = fresh()
    bot._last_seen_at = time.time() - 1
    bot.report_reconnect_gap(client, conn)
    assert client.sent == []
    assert conn.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 0


def test_resync_commands_replays_a_missed_command():
    client, conn = fresh()
    cursor_before = bot.cursor
    cursor_before.init_table(conn)
    cursor_before.set(conn, bot.LOG_CHANNEL, 5)
    client.respond(
        "POST",
        "/sync",
        {"scopes": [{"messages": [{"author_id": USER, "content": "!modlog stats", "id": "m9", "seq": 6}], "reset": False}]},
    )
    bot.resync_commands(client, conn, ME)
    assert client.sent[0]["content"] == "nothing recorded yet."
    assert cursor_before.get(conn, bot.LOG_CHANNEL) == 6


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
