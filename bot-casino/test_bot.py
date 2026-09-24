#!/usr/bin/env python3
"""Unit tests for `handle_message`'s own safeguards: ignoring bot and
webhook authors, the per-user command rate limit, and refusing to send
chips to a bot. Blackjack's own state machine is `test_blackjack.py`'s job;
money-safety under real concurrency is `test_concurrency.py`'s.

Run it directly, no test framework needed:

    python3 test_bot.py
"""

import os
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402
from slimbots import AuthorFilter  # noqa: E402
from slimbots.testing import FakeClient  # noqa: E402

ME = "bot-1"
CHANNEL = "chan-1"


def fresh_conn():
    # The rate limiter is a module-level singleton shared by every command,
    # and every test below reuses the same user id against one real wall
    # clock - reset it per test so one test's commands never count against
    # the next one's burst allowance.
    bot._command_limiter = bot.RateLimiter(bot.COMMANDS_PER_WINDOW, bot.COMMAND_WINDOW_SECONDS)
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("PRAGMA busy_timeout=5000")
    bot.init_db(conn)
    return conn


def incoming(author_id, content, message_id=None):
    return {"author_id": author_id, "content": content, "id": message_id or str(uuid.uuid4())}


def test_ignores_its_own_messages():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    authors = AuthorFilter(client)
    bot.handle_message(client, conn, ME, authors, CHANNEL, incoming(ME, "!daily"))
    assert client.sent == [], "a bot must never answer its own message"


def test_ignores_another_bots_messages():
    """The fix for the misfire this repo actually hit: a message posted by
    another bot (here, one whose text happens to be a command) must never
    be read as a command - see slimbots.AuthorFilter."""
    client, conn = FakeClient(me_id=ME), fresh_conn()
    client.respond("GET", "/users/other-bot", {"id": "other-bot", "is_bot": True})
    authors = AuthorFilter(client)
    bot.handle_message(client, conn, ME, authors, CHANNEL, incoming("other-bot", "!daily"))
    assert client.sent == [], "a bot's own output must never be read as a command by another bot"


def test_handles_a_human_authors_command():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    client.respond("GET", "/users/u1", {"id": "u1", "is_bot": False})
    authors = AuthorFilter(client)
    bot.handle_message(client, conn, ME, authors, CHANNEL, incoming("u1", "!balance"))
    assert len(client.sent) == 1
    assert "balance" in client.sent[0]["content"]


def test_command_rate_limit_refuses_past_the_burst_and_never_drops_silently():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    client.respond("GET", "/users/u1", {"id": "u1", "is_bot": False})
    authors = AuthorFilter(client)
    bot._command_limiter = bot.RateLimiter(3, 60)
    try:
        for _ in range(3):
            bot.handle_message(client, conn, ME, authors, CHANNEL, incoming("u1", "!balance"))
        client.sent.clear()
        bot.handle_message(client, conn, ME, authors, CHANNEL, incoming("u1", "!balance"))
        assert len(client.sent) == 1, "a refused command still gets a reply, never a silent drop"
        assert "slow down" in client.sent[0]["content"]
    finally:
        bot._command_limiter = bot.RateLimiter(bot.COMMANDS_PER_WINDOW, bot.COMMAND_WINDOW_SECONDS)


def test_ordinary_chat_is_not_charged_against_the_rate_limit():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    client.respond("GET", "/users/u1", {"id": "u1", "is_bot": False})
    authors = AuthorFilter(client)
    bot._command_limiter = bot.RateLimiter(1, 60)
    try:
        for _ in range(5):
            bot.handle_message(client, conn, ME, authors, CHANNEL, incoming("u1", "just chatting, not a command"))
        assert client.sent == [], "plain chat must never trigger any reply"
    finally:
        bot._command_limiter = bot.RateLimiter(bot.COMMANDS_PER_WINDOW, bot.COMMAND_WINDOW_SECONDS)


def test_give_refuses_a_bot_recipient():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    conn.execute("INSERT INTO accounts (user_id, balance, last_daily) VALUES ('u1', 1000, 0)")
    client.respond("GET", "/members?limit=200", [{"id": "other-bot", "username": "modlog", "display_name": "modlog"}])
    client.respond("GET", "/users/other-bot", {"id": "other-bot", "is_bot": True})
    authors = AuthorFilter(client)

    bot.handle_give(client, conn, CHANNEL, "u1", str(uuid.uuid4()), 10, "modlog", authors)

    assert "don't play" in client.sent[-1]["content"]
    assert bot.get_balance(conn, "u1") == 1000, "a refused give must never touch the sender's balance"


def test_give_refuses_an_amount_over_the_bound():
    client, conn = FakeClient(me_id=ME), fresh_conn()
    conn.execute("INSERT INTO accounts (user_id, balance, last_daily) VALUES ('u1', 1000, 0)")
    client.respond("GET", "/members?limit=200", [{"id": "u2", "username": "friend", "display_name": "friend"}])
    authors = AuthorFilter(client)

    bot.handle_give(client, conn, CHANNEL, "u1", str(uuid.uuid4()), bot.MAX_AMOUNT + 1, "friend", authors)

    assert "under" in client.sent[-1]["content"]
    assert bot.get_balance(conn, "u1") == 1000


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} bot.py safeguard tests passed")
