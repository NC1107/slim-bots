#!/usr/bin/env python3
"""Command-layer tests against slimbots.testing.FakeAsyncClient.

Run it directly, no test framework needed: python3 test_bot.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as reminders  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]


def message(content, msg_id="m1"):
    return {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content}


def setup():
    reminders.bot.db = reminders.sqlite3.connect(":memory:")
    reminders.init_db(reminders.bot.db)
    reminders.bot.channels = {"c1"}
    reminders._command_limiter._hits.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    reminders.bot.client = client
    reminders.bot.space = Space(client)
    reminders.bot.authors = AuthorFilter(client, space=reminders.bot.space, ignore_bots=True)
    reminders.bot.me_id = "bot-1"
    asyncio.run(reminders.bot.space.refresh_members())
    return client


def process(client, *messages):
    async def run():
        for msg in messages:
            await reminders.bot.process_message(msg)

    asyncio.run(run())


def test_remind_in_creates_a_reminder_and_acks():
    client = setup()
    process(client, message("!remind me in 2h water the plants"))
    assert "will remind you" in client.sent[-1]["content"]
    rows = reminders.pending_for_user(reminders.bot.db, "c1", "u1")
    assert len(rows) == 1
    assert rows[0][2] == "water the plants"


def test_remind_in_bad_duration_is_refused_without_creating_one():
    client = setup()
    process(client, message("!remind me in banana water the plants"))
    assert "not a duration" in client.sent[-1]["content"]
    assert reminders.pending_for_user(reminders.bot.db, "c1", "u1") == []


def test_remind_every_weekday_recurs():
    client = setup()
    process(client, message("!remind me every monday at 09:00 standup"))
    assert "every monday at 09:00" in client.sent[-1]["content"]


def test_remind_every_interval_below_the_floor_is_refused():
    client = setup()
    process(client, message("!remind me every 10s spam"))
    assert "at least" in client.sent[-1]["content"]


def test_reminders_lists_pending():
    client = setup()
    process(client, message("!remind me in 1h water the plants", "m1"))
    process(client, message("!reminders", "m2"))
    assert "water the plants" in client.sent[-1]["content"]


def test_reminders_cancel_removes_it():
    client = setup()
    process(client, message("!remind me in 1h water the plants", "m1"))
    process(client, message("!reminders cancel 1", "m2"))
    assert "cancelled reminder 1" in client.sent[-1]["content"]
    assert reminders.pending_for_user(reminders.bot.db, "c1", "u1") == []


def test_reminders_cancel_out_of_range_says_so():
    client = setup()
    process(client, message("!reminders cancel 5"))
    assert "no reminder 5" in client.sent[-1]["content"]


def test_reminders_edit_changes_the_text():
    client = setup()
    process(client, message("!remind me in 1h old text", "m1"))
    process(client, message("!reminders edit 1 new text", "m2"))
    assert "updated reminder 1" in client.sent[-1]["content"]
    rows = reminders.pending_for_user(reminders.bot.db, "c1", "u1")
    assert rows[0][2] == "new text"


def test_reminders_snooze_pushes_the_due_time():
    client = setup()
    process(client, message("!remind me in 1h water the plants", "m1"))
    before = reminders.pending_for_user(reminders.bot.db, "c1", "u1")[0][1]
    process(client, message("!reminders snooze 1 30m", "m2"))
    after = reminders.pending_for_user(reminders.bot.db, "c1", "u1")[0][1]
    assert after == before + 1800


def test_timezone_set_and_show():
    client = setup()
    process(client, message("!timezone America/New_York", "m1"))
    assert "timezone set" in client.sent[-1]["content"]
    process(client, message("!timezone", "m2"))
    assert "America/New_York" in client.sent[-1]["content"]


def test_timezone_rejects_an_unknown_name():
    client = setup()
    process(client, message("!timezone Mars/Nowhere"))
    assert "isn't a timezone" in client.sent[-1]["content"]


def test_pending_cap_refuses_a_new_reminder():
    client = setup()
    for i in range(reminders.MAX_PENDING_PER_USER):
        reminders.add_reminder(reminders.bot.db, f"r{i}", "c1", "u1", "m0", int(time.time()) + 3600, "x")
    process(client, message("!remind me in 1h one more"))
    assert "cancel one first" in client.sent[-1]["content"]


def test_due_reminder_is_delivered_with_a_backtick_wrapped_and_embed():
    client = setup()
    reminders.add_reminder(reminders.bot.db, "r1", "c1", "u1", "m1", int(time.time()) - 1, "check the oven")

    async def run_one_pass():
        due = reminders.due_reminders(reminders.bot.db, int(time.time()))
        for row in due:
            (reminder_id, channel_id, request_message_id, text, due_at,
             recur_kind, interval_seconds, weekday, hour, minute, tz_name) = row
            embed_text = f"reminder: {reminders.render_reminder_text(text)}"
            await reminders.bot.client.send(channel_id, embed_text, message_id=reminders.delivery_id(reminder_id, due_at), reply_to_id=request_message_id)
            reminders.mark_sent(reminders.bot.db, reminder_id)

    asyncio.run(run_one_pass())
    assert client.sent[-1]["content"] == "reminder: `check the oven`"
    assert reminders.due_reminders(reminders.bot.db, int(time.time())) == []


def test_another_bot_is_ignored_by_default():
    client = setup()
    client.respond(
        "GET", "/members",
        MEMBERS + [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []}],
    )
    asyncio.run(reminders.bot.space.refresh_members())
    process(client, {"id": "m1", "author_id": "bot-2", "channel_id": "c1", "content": "!remind me in 1h daily"})
    assert client.sent == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
