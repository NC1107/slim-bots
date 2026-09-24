#!/usr/bin/env python3
"""Unit tests for the blackjack state machine (`blackjack.py` plus the
`handle_*` functions in `bot.py` that drive it): double down, split, and
surrender, and the guards that keep each to the one situation it is meant
for. Money-safety under real concurrency is `test_concurrency.py`'s job, not
this file's - these tests run one command at a time, deterministically, by
fixing the "shoe" (`bot.draw_card`) to a known sequence of cards.

Run it directly, no test framework needed:

    python3 test_blackjack.py
"""

import os
import sqlite3
import sys
import uuid
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402
from slimbots.testing import FakeClient  # noqa: E402

CHANNEL = "chan-1"


def fresh_conn():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("PRAGMA busy_timeout=5000")
    bot.init_db(conn)
    return conn


def new_user(conn, balance=1000):
    user_id = str(uuid.uuid4())
    conn.execute("INSERT INTO accounts (user_id, balance, last_daily) VALUES (?, ?, 0)", (user_id, balance))
    return user_id


@contextmanager
def fixed_shoe(*cards):
    """Replaces `bot.draw_card` with a fixed sequence for the duration of the
    block, so a test can force a specific deal instead of retrying until
    `secrets.choice` cooperates."""
    remaining = list(cards)
    original = bot.draw_card
    bot.draw_card = lambda: remaining.pop(0)
    try:
        yield
    finally:
        bot.draw_card = original


def deal(client, conn, user_id, amount="100"):
    """Starts a hand. Must be called inside a `fixed_shoe` context that
    covers the deal and every card any later action in the same test will
    draw - `draw_card` is only fixed for as long as that context is open."""
    bot.handle_blackjack_start(client, conn, CHANNEL, user_id, str(uuid.uuid4()), amount)


def test_double_down_doubles_the_stake_and_ends_the_hand():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("3H", "JD", "AS", "6S", "5D"):
        deal(client, conn, user)
        client.sent.clear()
        bot.handle_double(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert not bot.blackjack.has_round(conn, CHANNEL, user), "double down must end the hand"
    # 13 + 5D = 18 beats dealer's 17 (AS 6S): staked 200 total, paid 2x = 400
    assert bot.get_balance(conn, user) == 1000 - 200 + 400
    assert "18" in client.sent[-1]["content"]


def test_double_down_refuses_after_a_hit():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("3H", "4D", "2S", "3S", "2H"):
        deal(client, conn, user)
        bot.handle_hit(client, conn, CHANNEL, user, str(uuid.uuid4()))
        client.sent.clear()
        bot.handle_double(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert "first two cards" in client.sent[-1]["content"]
    assert bot.blackjack.has_round(conn, CHANNEL, user), "a refused double must not end the hand"


def test_double_down_refuses_without_enough_balance():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 150)
    with fixed_shoe("3H", "4D", "2S", "3S"):
        deal(client, conn, user)
        client.sent.clear()
        bot.handle_double(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert "don't have that many chips" in client.sent[-1]["content"]
    assert bot.get_balance(conn, user) == 50, "a refused double must not touch the balance further"


def test_split_creates_two_independent_hands():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("8H", "8D", "2C", "2S", "7D", "6D"):
        deal(client, conn, user)
        client.sent.clear()
        bot.handle_split(client, conn, CHANNEL, user, str(uuid.uuid4()))

    rows = bot.blackjack.round_hands(conn, CHANNEL, user)
    assert [r[0] for r in rows] == [0, 1]
    assert rows[0][1] == rows[1][1] == 100, "each split hand stakes the original amount"
    assert bot.get_balance(conn, user) == 800, "splitting debits a second stake"


def test_split_refuses_unequal_cards():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("8H", "9D", "2C", "2S"):
        deal(client, conn, user)
        client.sent.clear()
        bot.handle_split(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert "can't be split" in client.sent[-1]["content"]


def test_cannot_resplit_a_split_hand():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("8H", "8D", "2C", "2S", "8S", "3C"):
        deal(client, conn, user)
        bot.handle_split(client, conn, CHANNEL, user, str(uuid.uuid4()))
        client.sent.clear()
        bot.handle_split(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert "can't be split" in client.sent[-1]["content"]


def test_playing_both_split_hands_settles_each_against_one_dealer_hand():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("8H", "8D", "6C", "10S", "9D", "9S", "2D"):
        deal(client, conn, user)
        bot.handle_split(client, conn, CHANNEL, user, str(uuid.uuid4()))
        client.sent.clear()
        bot.handle_stand(client, conn, CHANNEL, user, str(uuid.uuid4()))
        bot.handle_stand(client, conn, CHANNEL, user, str(uuid.uuid4()))  # dealer draws 2D: 16 -> 18

    assert not bot.blackjack.has_round(conn, CHANNEL, user)
    # hand 1: 8+9=17 loses to dealer 18. hand 2: 8+9=17 loses to dealer 18.
    assert bot.get_balance(conn, user) == 800
    final = client.sent[-1]["content"]
    assert "hand 1" in final and "hand 2" in final and "dealer" in final


def test_surrender_refunds_half_and_ends_the_hand():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("3H", "QD", "5S", "9C"):
        deal(client, conn, user)
        client.sent.clear()
        bot.handle_surrender(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert bot.get_balance(conn, user) == 950
    assert not bot.blackjack.has_round(conn, CHANNEL, user)
    assert "surrendered" in client.sent[-1]["content"]


def test_surrender_refuses_after_a_split():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("8H", "8D", "2C", "2S", "3D", "4C"):
        deal(client, conn, user)
        bot.handle_split(client, conn, CHANNEL, user, str(uuid.uuid4()))
        client.sent.clear()
        bot.handle_surrender(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert "only offered" in client.sent[-1]["content"]


def test_surrender_refuses_after_a_hit():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("3H", "4D", "2S", "3S", "2H"):
        deal(client, conn, user)
        bot.handle_hit(client, conn, CHANNEL, user, str(uuid.uuid4()))
        client.sent.clear()
        bot.handle_surrender(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert "only offered" in client.sent[-1]["content"]


def test_a_bust_hand_needs_no_dealer_draw():
    client, conn = FakeClient(), fresh_conn()
    user = new_user(conn, 1000)
    with fixed_shoe("6H", "9D", "2S", "3S", "KH"):
        deal(client, conn, user)
        client.sent.clear()
        bot.handle_hit(client, conn, CHANNEL, user, str(uuid.uuid4()))

    assert bot.get_balance(conn, user) == 900, "a bust loses only the original stake"
    assert "bust" in client.sent[-1]["content"]
    assert "dealer:" not in client.sent[-1]["content"], "a fully-bust round never needed to play the dealer out"


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} blackjack tests passed")
