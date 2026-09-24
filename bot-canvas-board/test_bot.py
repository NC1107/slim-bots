#!/usr/bin/env python3
"""Unit tests for bot.py's command handling, using slimbots.testing.FakeClient
so none of this needs a live deployment, a socket, or a real canvas.

Run it directly, no test framework needed:

    pip install -r requirements.txt
    python3 test_bot.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402
from slimbots import AuthorFilter  # noqa: E402
from slimbots.testing import FakeClient  # noqa: E402

ME = "bot-1"
USER = "user-1"
OTHER_BOT = "bot-2"
bot.CHANNEL = "chan-1"

CANVAS_OBJECTS = f"/channels/{bot.CHANNEL}/canvas/objects"
CANVAS_OPS = f"/channels/{bot.CHANNEL}/canvas/ops"


def fresh():
    bot._names.clear()
    client = FakeClient(me_id=ME)
    client.respond("GET", f"/users/{USER}", {"is_bot": False, "display_name": "nick"})
    client.respond("GET", f"/users/{OTHER_BOT}", {"is_bot": True, "display_name": "some-bot"})
    conn = sqlite3.connect(":memory:")
    bot.init_db(conn)
    return client, conn, AuthorFilter(client)


def incoming(content, author=USER, message_id="m1"):
    return {"author_id": author, "content": content, "id": message_id, "channel_id": bot.CHANNEL}


def test_ignores_its_own_messages():
    client, conn, authors = fresh()
    bot.handle_message(client, conn, ME, authors, incoming("!board add hi", author=ME))
    assert client.sent == []


def test_ignores_another_bot():
    client, conn, authors = fresh()
    bot.handle_message(client, conn, ME, authors, incoming("!board add hi", author=OTHER_BOT))
    assert client.sent == []


def test_add_places_a_note_and_credits_the_author():
    client, conn, authors = fresh()
    client.respond("POST", CANVAS_OBJECTS, {"seq": 1})
    bot.handle_message(client, conn, ME, authors, incoming("!board add buy milk"))
    assert "added as #1: buy milk" in client.sent[0]["content"]

    client.sent.clear()
    bot.handle_message(client, conn, ME, authors, incoming("!board"))
    assert client.sent[0]["content"] == "#1: buy milk (added by nick)"


def test_add_refuses_text_past_the_length_bound():
    client, conn, authors = fresh()
    bot.handle_message(client, conn, ME, authors, incoming("!board add " + "x" * (bot.MAX_TEXT_LENGTH + 1)))
    assert "max" in client.sent[0]["content"]
    assert bot.active_items(conn) == []


def test_board_full_refuses_a_new_note():
    client, conn, authors = fresh()
    client.respond("POST", CANVAS_OBJECTS, lambda: {"seq": 1})
    for i in range(bot.MAX_SLOTS):
        bot.add_item(client, conn, bot.CHANNEL, f"m{i}", USER, f"item {i}")
    client.sent.clear()
    bot.handle_message(client, conn, ME, authors, incoming("!board add one too many"))
    assert "board is full" in client.sent[0]["content"]


def test_done_removes_the_right_item():
    client, conn, authors = fresh()
    client.respond("POST", CANVAS_OBJECTS, {"seq": 1})
    client.respond("POST", CANVAS_OPS, {})
    bot.handle_message(client, conn, ME, authors, incoming("!board add buy milk"))
    client.sent.clear()
    bot.handle_message(client, conn, ME, authors, incoming("!board done 1"))
    assert client.sent[0]["content"] == "done: buy milk"
    assert bot.active_items(conn) == []


def test_done_on_an_empty_slot_says_so():
    client, conn, authors = fresh()
    bot.handle_message(client, conn, ME, authors, incoming("!board done 3"))
    assert "no item #3" in client.sent[0]["content"]


def test_clear_without_confirmation_does_nothing():
    client, conn, authors = fresh()
    client.respond("POST", CANVAS_OBJECTS, {"seq": 1})
    bot.handle_message(client, conn, ME, authors, incoming("!board add buy milk"))
    client.sent.clear()
    bot.handle_message(client, conn, ME, authors, incoming("!board clear"))
    assert "clear yes" in client.sent[0]["content"]
    assert len(bot.active_items(conn)) == 1


def test_clear_yes_removes_everything():
    client, conn, authors = fresh()
    client.respond("POST", CANVAS_OBJECTS, {"seq": 1})
    client.respond("POST", CANVAS_OPS, {})
    bot.handle_message(client, conn, ME, authors, incoming("!board add buy milk"))
    bot.handle_message(client, conn, ME, authors, incoming("!board add walk dog", message_id="m2"))
    client.sent.clear()
    bot.handle_message(client, conn, ME, authors, incoming("!board clear yes"))
    assert "cleared 2" in client.sent[0]["content"]
    assert bot.active_items(conn) == []


def test_clear_on_an_empty_board_says_so():
    client, conn, authors = fresh()
    bot.handle_message(client, conn, ME, authors, incoming("!board clear yes"))
    assert "already empty" in client.sent[0]["content"]


def test_malformed_board_command_gets_the_help_text():
    client, conn, authors = fresh()
    bot.handle_message(client, conn, ME, authors, incoming("!board add"))
    assert client.sent[0]["content"] == bot.HELP_TEXT


def test_board_help_gets_the_help_text():
    client, conn, authors = fresh()
    bot.handle_message(client, conn, ME, authors, incoming("!board help"))
    assert client.sent[0]["content"] == bot.HELP_TEXT


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
