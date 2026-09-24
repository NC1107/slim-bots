#!/usr/bin/env python3
"""Command-layer tests against FakeAsyncClient (test_concurrency.py covers money-safety); run directly: python3 test_bot.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SLIMM_CHANNELS", "c1")

import bot as casino  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [
    {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []},
    {"id": "u2", "username": "sam", "display_name": "Sam", "is_bot": False, "is_webhook": False, "role_ids": []},
    {"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []},
]


def message(author_id, content, msg_id="m1"):
    return {"id": msg_id, "author_id": author_id, "channel_id": "c1", "content": content}


def setup():
    """Fresh in-memory db and a fresh FakeAsyncClient wired onto the module-level bot."""
    casino.bot.store = Store(":memory:", migrate=casino.init_db)
    asyncio.run(casino.bot.store.open())
    casino._command_limiter._hits.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    casino.bot.client = client
    casino.bot.space = Space(client)
    casino.bot.authors = AuthorFilter(client, space=casino.bot.space, ignore_bots=True)
    casino.bot.me_id = "bot-1"
    asyncio.run(casino.bot.space.refresh_members())
    return client


def script_cards(cards):
    remaining = list(cards)

    def fake_draw():
        return remaining.pop(0)

    casino.draw_card = fake_draw


def restore_draw_card(original):
    casino.draw_card = original


def process(client, *messages):
    async def run():
        for msg in messages:
            await casino.bot.process_message(msg)

    asyncio.run(run())


def test_balance_starts_at_zero():
    client = setup()
    process(client, message("u1", "!balance"))
    sent = client.sent[-1]
    assert sent["embeds"][0]["fields"] == [{"name": "chips", "value": "0", "inline": False}]


def test_daily_credits_and_then_cools_down():
    client = setup()
    process(client, message("u1", "!daily", "m1"))
    assert "claimed 500 chips" in client.sent[-1]["content"]
    process(client, message("u1", "!daily", "m2"))
    assert "already claimed" in client.sent[-1]["content"]


def test_give_moves_chips_between_accounts():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    process(client, message("u1", "!give 30 sam", "m1"))
    assert "sent 30 chips to Sam" in client.sent[-1]["content"]
    assert casino.get_balance(casino.bot.store.connection, "u1") == 70
    assert casino.get_balance(casino.bot.store.connection, "u2") == 30


def test_give_refuses_yourself():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    process(client, message("u1", "!give 10 nick", "m1"))
    assert "yourself" in client.sent[-1]["content"]


def test_give_refuses_a_bot_recipient():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    process(client, message("u1", "!give 10 otherbot", "m1"))
    assert "bots don't play" in client.sent[-1]["content"]


def test_give_refuses_insufficient_funds():
    client = setup()
    process(client, message("u1", "!give 10 sam", "m1"))
    assert "don't have that many chips" in client.sent[-1]["content"]


def test_give_refuses_over_max_amount():
    client = setup()
    process(client, message("u1", f"!give {casino.MAX_AMOUNT + 1} sam", "m1"))
    assert "keep a single transfer under" in client.sent[-1]["content"]


def test_flip_rejects_a_bad_guess():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    process(client, message("u1", "!flip 10 maybe", "m1"))
    assert "call `heads`" in client.sent[-1]["content"]
    assert casino.get_balance(casino.bot.store.connection, "u1") == 100


def test_flip_rejects_a_bad_amount():
    client = setup()
    process(client, message("u1", "!flip five heads", "m1"))
    assert "whole number" in client.sent[-1]["content"]


def test_flip_wins():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    original = casino.secrets.choice
    casino.secrets.choice = lambda seq: "heads"
    try:
        process(client, message("u1", "!flip 10 heads", "m1"))
    finally:
        casino.secrets.choice = original
    assert "you called it" in client.sent[-1]["content"]
    assert casino.get_balance(casino.bot.store.connection, "u1") == 109


def test_flip_loses():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    original = casino.secrets.choice
    casino.secrets.choice = lambda seq: "tails"
    try:
        process(client, message("u1", "!flip 10 heads", "m1"))
    finally:
        casino.secrets.choice = original
    assert "-10 chips" in client.sent[-1]["content"]
    assert casino.get_balance(casino.bot.store.connection, "u1") == 90


def test_blackjack_natural_win_pays_three_to_two():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    original = casino.draw_card
    script_cards(["AH", "KH", "2C", "5D"])
    try:
        process(client, message("u1", "!blackjack 10", "m1"))
    finally:
        restore_draw_card(original)
    assert "blackjack!" in client.sent[-1]["content"]
    assert casino.get_balance(casino.bot.store.connection, "u1") == 115


def test_blackjack_hit_then_bust():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    original = casino.draw_card
    script_cards(["7H", "8H", "2C", "5D", "KC"])
    try:
        process(client, message("u1", "!blackjack 10", "m1"), message("u1", "!hit", "m2"))
    finally:
        restore_draw_card(original)
    assert "bust" in client.sent[-1]["content"]
    assert casino.get_balance(casino.bot.store.connection, "u1") == 90


def test_blackjack_double_doubles_the_stake():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    original = casino.draw_card
    script_cards(["5H", "6H", "2C", "5D", "KC", "QC"])
    try:
        process(client, message("u1", "!blackjack 10", "m1"), message("u1", "!double", "m2"))
    finally:
        restore_draw_card(original)
    assert "balance" in client.sent[-1]["content"]
    assert casino.get_balance(casino.bot.store.connection, "u1") == 120


def test_blackjack_split_creates_two_hands():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    original = casino.draw_card
    script_cards(["8H", "8D", "2C", "5D", "3H", "4H"])
    try:
        process(client, message("u1", "!blackjack 10", "m1"), message("u1", "!split", "m2"))
    finally:
        restore_draw_card(original)
    assert "split into two hands" in client.sent[-1]["content"]
    assert casino.blackjack.has_round(casino.bot.store.connection, "c1", "u1")


def test_blackjack_surrender_refunds_half():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    original = casino.draw_card
    script_cards(["9H", "6H", "2C", "5D"])
    try:
        process(client, message("u1", "!blackjack 10", "m1"), message("u1", "!surrender", "m2"))
    finally:
        restore_draw_card(original)
    assert "surrendered" in client.sent[-1]["content"]
    assert casino.get_balance(casino.bot.store.connection, "u1") == 95


def test_rate_limiter_kicks_in_after_a_burst():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 1000)
    messages = [message("u1", "!balance", f"m{i}") for i in range(casino.COMMANDS_PER_WINDOW)]
    messages.append(message("u1", "!balance", "m-over"))
    process(client, *messages)
    assert "slow down" in client.sent[-1]["content"]


def test_a_duplicate_request_id_is_a_no_op():
    client = setup()
    casino.credit(casino.bot.store.connection, "u1", 100)
    process(client, message("u1", "!give 10 sam", "dupe-1"), message("u1", "!give 10 sam", "dupe-1"))
    assert casino.get_balance(casino.bot.store.connection, "u1") == 90
    assert casino.get_balance(casino.bot.store.connection, "u2") == 10


def test_another_bot_is_ignored_by_default():
    client = setup()
    process(client, message("bot-2", "!daily", "m1"))
    assert client.sent == []


def test_on_ready_starts_maintenance_as_a_supervised_background_task():
    setup()
    casino._maintenance_started = False

    async def run():
        await casino.on_ready()
        assert len(casino.bot._background_tasks) == 1
        task = next(iter(casino.bot._background_tasks))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
