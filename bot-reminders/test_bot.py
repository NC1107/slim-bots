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
from slimbots import AuthorFilter  # noqa: E402
from slimbots.testing import FakeClient  # noqa: E402

ME = "bot-1"
USER = "user-1"
CHANNEL = "chan-1"


def fresh_conn():
    # Every test shares the same USER id against one real wall clock, and
    # the rate limiter is a module-level singleton (matching bot-casino's
    # own pattern) - reset it per test so one test's commands never count
    # against the next one's burst allowance.
    bot._command_limiter = bot.RateLimiter(bot.COMMANDS_PER_WINDOW, bot.COMMAND_WINDOW_SECONDS)
    conn = sqlite3.connect(":memory:")
    bot.init_db(conn)
    return conn


def fresh_authors(client):
    client.respond("GET", f"/users/{USER}", {"id": USER, "is_bot": False})
    return AuthorFilter(client)


def incoming(content, message_id="m1", author_id=USER):
    return {"author_id": author_id, "content": content, "id": message_id}


def send(client, conn, authors, content, message_id="m1", author_id=USER):
    bot.handle_message(client, conn, ME, authors, CHANNEL, incoming(content, message_id, author_id))


def test_bot_ignores_its_own_messages():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me in 1h hi", author_id=ME)
    assert client.sent == [], "a bot must never answer its own message"


def test_bot_ignores_another_bots_messages():
    """The fix for the misfire this repo actually hit in reverse: a message
    from another bot must never be read as a command here either, even one
    that happens to look exactly like `!remind`."""
    client, conn = FakeClient(me_id=ME), fresh_conn()
    client.respond("GET", "/users/other-bot", {"id": "other-bot", "is_bot": True})
    authors = AuthorFilter(client)
    send(client, conn, authors, "!remind me in 1h hi", author_id="other-bot")
    assert client.sent == []


def test_remind_in_duration_creates_a_reminder_and_acks():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me in 2h water the plants")

    assert len(client.sent) == 1
    assert "will remind you" in client.sent[0]["content"]
    rows = bot.pending_for_user(conn, CHANNEL, USER)
    assert len(rows) == 1
    assert rows[0][2] == "water the plants"


def test_remind_in_bad_duration_is_refused_without_creating_one():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me in banana water the plants")

    assert "not a duration" in client.sent[0]["content"]
    assert bot.pending_for_user(conn, CHANNEL, USER) == []


def test_remind_text_over_the_bound_is_refused():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, f"!remind me in 1h {'x' * (bot.MAX_TEXT_LEN + 1)}")

    assert "too long" in client.sent[0]["content"]
    assert bot.pending_for_user(conn, CHANNEL, USER) == []


def test_pending_quota_refuses_past_the_limit():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    for i in range(bot.MAX_PENDING_PER_USER):
        bot.add_reminder(conn, f"r{i}", CHANNEL, USER, "m", int(time.time()) + 3600, "x")

    send(client, conn, authors, "!remind me in 1h one too many")

    assert "already have" in client.sent[0]["content"]
    assert len(bot.pending_for_user(conn, CHANNEL, USER)) == bot.MAX_PENDING_PER_USER


def test_reminders_lists_pending_in_order():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me in 2h second", "m1")
    send(client, conn, authors, "!remind me in 1h first", "m2")
    client.sent.clear()

    send(client, conn, authors, "!reminders", "m3")

    listing = client.sent[0]["content"]
    assert listing.index("first") < listing.index("second"), "listed in due-date order, not creation order"


def test_reminders_cancel_removes_the_right_one():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me in 1h first", "m1")
    send(client, conn, authors, "!remind me in 2h second", "m2")
    client.sent.clear()

    send(client, conn, authors, "!reminders cancel 1", "m3")

    assert "cancelled reminder 1" in client.sent[0]["content"]
    remaining = bot.pending_for_user(conn, CHANNEL, USER)
    assert len(remaining) == 1
    assert remaining[0][2] == "second"


def test_reminders_cancel_out_of_range_says_so():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!reminders cancel 5")
    assert "no reminder 5" in client.sent[0]["content"]


def test_reminders_edit_changes_the_text():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me in 1h old text", "m1")
    client.sent.clear()

    send(client, conn, authors, "!reminders edit 1 new text", "m2")

    assert "updated reminder 1" in client.sent[0]["content"]
    rows = bot.pending_for_user(conn, CHANNEL, USER)
    assert rows[0][2] == "new text"


def test_reminders_snooze_pushes_the_due_time_back():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me in 1h check", "m1")
    before = bot.pending_for_user(conn, CHANNEL, USER)[0][1]
    client.sent.clear()

    send(client, conn, authors, "!reminders snooze 1 30m", "m2")

    after = bot.pending_for_user(conn, CHANNEL, USER)[0][1]
    assert after - before == 1800
    assert "pushed to" in client.sent[0]["content"]


def test_timezone_set_and_show():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!timezone America/New_York", "m1")
    assert "set to" in client.sent[0]["content"]
    client.sent.clear()

    send(client, conn, authors, "!timezone", "m2")
    assert "America/New_York" in client.sent[0]["content"]


def test_timezone_rejects_an_unknown_name():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!timezone Not/AZone", "m1")
    assert "isn't a timezone" in client.sent[0]["content"]
    assert bot.get_timezone(conn, USER) == "UTC"


def test_remind_every_weekday_creates_a_weekly_recurrence():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me every monday at 09:00 standup")

    rows = bot.pending_for_user(conn, CHANNEL, USER)
    assert len(rows) == 1
    _, _, text, recur_kind, _, weekday, hour, minute, _ = rows[0]
    assert (text, recur_kind, weekday, hour, minute) == ("standup", "weekly", 0, 9, 0)
    assert "every" in client.sent[0]["content"]


def test_remind_every_interval_creates_an_interval_recurrence():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me every 2h drink water")

    rows = bot.pending_for_user(conn, CHANNEL, USER)
    assert rows[0][3] == "interval"
    assert rows[0][4] == 7200


def test_remind_every_refuses_a_too_short_interval():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    send(client, conn, authors, "!remind me every 1s spam")

    assert "must be at least" in client.sent[0]["content"]
    assert bot.pending_for_user(conn, CHANNEL, USER) == []


def test_a_one_off_reminder_is_marked_sent_and_wrapped_in_a_code_span():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    bot.add_reminder(conn, "reminder-1", CHANNEL, USER, "m1", int(time.time()) - 1, "!daily")

    due = bot.due_reminders(conn, int(time.time()))
    assert len(due) == 1
    (reminder_id, channel_id, request_message_id, text, due_at, recur_kind, *_rest) = due[0]
    client.send(
        channel_id,
        f"reminder: {bot.render_reminder_text(text)}",
        message_id=bot.delivery_id(reminder_id, due_at),
        reply_to_id=request_message_id,
    )
    bot.mark_sent(conn, reminder_id)

    assert client.sent[0]["content"] == "reminder: `!daily`"
    assert not client.sent[0]["content"].startswith("!"), "must not look like a command to another bot"
    assert bot.due_reminders(conn, int(time.time())) == []


def test_a_recurring_reminder_is_rescheduled_not_marked_sent():
    conn = fresh_conn()
    now = int(time.time())
    bot.add_reminder(
        conn, "reminder-1", CHANNEL, USER, "m1", now - 1, "drink water",
        recur={"kind": "interval", "interval_seconds": 3600, "tz": "UTC"},
    )

    due = bot.due_reminders(conn, now)
    reminder_id, channel_id, request_message_id, text, due_at, recur_kind, interval_seconds, *_rest = due[0]
    assert recur_kind == "interval"
    bot.reschedule(conn, reminder_id, bot.recurrence.next_interval(due_at, interval_seconds, now))

    still_pending = bot.pending_for_user(conn, CHANNEL, USER)
    assert len(still_pending) == 1, "a recurring reminder must still be pending after it fires"
    assert bot.due_reminders(conn, now) == [], "and not due again immediately"


def test_delivery_id_differs_between_firings_but_is_stable_within_one():
    first = bot.delivery_id("r1", 1000)
    again = bot.delivery_id("r1", 1000)
    second_firing = bot.delivery_id("r1", 2000)
    assert first == again, "retrying the same firing must reuse its id"
    assert first != second_firing, "the next firing must get its own id"


def test_prune_removes_only_old_sent_or_cancelled_reminders():
    conn = fresh_conn()
    old = int(time.time()) - bot.REMINDER_RETENTION_SECONDS - 10
    conn.execute(
        "INSERT INTO reminders (id, channel_id, user_id, request_message_id, due_at, text, sent, cancelled, created_at) "
        "VALUES ('old-sent', ?, ?, 'm', ?, 'x', 1, 0, ?)",
        (CHANNEL, USER, old, old),
    )
    conn.execute(
        "INSERT INTO reminders (id, channel_id, user_id, request_message_id, due_at, text, sent, cancelled, created_at) "
        "VALUES ('old-pending', ?, ?, 'm', ?, 'x', 0, 0, ?)",
        (CHANNEL, USER, old, old),
    )
    conn.commit()

    bot.prune_old_reminders(conn, int(time.time()) - bot.REMINDER_RETENTION_SECONDS)

    remaining = {row[0] for row in conn.execute("SELECT id FROM reminders")}
    assert remaining == {"old-pending"}, "only a resolved (sent or cancelled) reminder is ever pruned"


def test_command_rate_limit_refuses_past_the_burst():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = fresh_authors(client)
    bot._command_limiter = bot.RateLimiter(2, 60)
    try:
        send(client, conn, authors, "!reminders", "m1")
        send(client, conn, authors, "!reminders", "m2")
        client.sent.clear()
        send(client, conn, authors, "!reminders", "m3")
        assert len(client.sent) == 1
        assert "slow down" in client.sent[0]["content"]
    finally:
        bot._command_limiter = bot.RateLimiter(bot.COMMANDS_PER_WINDOW, bot.COMMAND_WINDOW_SECONDS)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
