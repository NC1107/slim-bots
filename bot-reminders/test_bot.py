#!/usr/bin/env python3
"""Unit tests for bot.py's command handling, using slimbots.testing.FakeClient
so none of this needs a live deployment or a socket. What it does not cover:
`due_checker`'s actual sleep loop, and anything about the websocket
reconnect itself - those are `slimbots`' own responsibility, tested there.

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
CHANNEL = "chan-1"


def fresh_conn():
    conn = sqlite3.connect(":memory:")
    bot.init_db(conn)
    return conn


def incoming(content, message_id="m1"):
    return {"author_id": USER, "content": content, "id": message_id}


def test_bot_ignores_its_own_messages():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    bot.handle_message(client, conn, ME, CHANNEL, {"author_id": ME, "content": "!remind me in 1h hi", "id": "m1"})
    assert client.sent == [], "a bot must never answer its own message"


def test_remind_in_duration_creates_a_reminder_and_acks():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    bot.handle_message(client, conn, ME, CHANNEL, incoming("!remind me in 2h water the plants"))

    assert len(client.sent) == 1
    assert "will remind you" in client.sent[0]["content"]
    rows = bot.pending_for_user(conn, CHANNEL, USER)
    assert len(rows) == 1
    assert rows[0][2] == "water the plants"


def test_remind_in_bad_duration_is_refused_without_creating_one():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    bot.handle_message(client, conn, ME, CHANNEL, incoming("!remind me in banana water the plants"))

    assert "not a duration" in client.sent[0]["content"]
    assert bot.pending_for_user(conn, CHANNEL, USER) == []


def test_reminders_lists_pending_in_order():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    bot.handle_message(client, conn, ME, CHANNEL, incoming("!remind me in 2h second", "m1"))
    bot.handle_message(client, conn, ME, CHANNEL, incoming("!remind me in 1h first", "m2"))
    client.sent.clear()

    bot.handle_message(client, conn, ME, CHANNEL, incoming("!reminders", "m3"))

    listing = client.sent[0]["content"]
    assert listing.index("first") < listing.index("second"), "listed in due-date order, not creation order"


def test_reminders_cancel_removes_the_right_one():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    bot.handle_message(client, conn, ME, CHANNEL, incoming("!remind me in 1h first", "m1"))
    bot.handle_message(client, conn, ME, CHANNEL, incoming("!remind me in 2h second", "m2"))
    client.sent.clear()

    bot.handle_message(client, conn, ME, CHANNEL, incoming("!reminders cancel 1", "m3"))

    assert "cancelled reminder 1" in client.sent[0]["content"]
    remaining = bot.pending_for_user(conn, CHANNEL, USER)
    assert len(remaining) == 1
    assert remaining[0][2] == "second"


def test_reminders_cancel_out_of_range_says_so():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    bot.handle_message(client, conn, ME, CHANNEL, incoming("!reminders cancel 5"))
    assert "no reminder 5" in client.sent[0]["content"]


def test_a_due_reminder_is_sent_with_its_own_id_and_marked_sent():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    reminder_id = "reminder-1"
    bot.add_reminder(conn, reminder_id, CHANNEL, USER, "m1", int(time.time()) - 1, "check the oven")

    due = bot.due_reminders(conn, int(time.time()))
    assert len(due) == 1
    for rid, channel_id, request_message_id, text in due:
        client.send(channel_id, f"reminder: {text}", message_id=rid, reply_to_id=request_message_id)
        bot.mark_sent(conn, rid)

    assert client.sent[0]["id"] == reminder_id
    assert client.sent[0]["content"] == "reminder: check the oven"
    assert bot.due_reminders(conn, int(time.time())) == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
