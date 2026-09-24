#!/usr/bin/env python3
"""Command-and-event tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as board  # noqa: E402
from slimbots import Canvas  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]


def message(content, msg_id="m1"):
    return {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content}


def setup():
    board.bot.db = board.sqlite3.connect(":memory:")
    board.init_db(board.bot.db)
    board.bot.channels = {"c1"}
    board._names.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    board.bot.client = client
    board.bot.space = Space(client)
    board.bot.authors = AuthorFilter(client, space=board.bot.space, ignore_bots=True)
    board.bot.me_id = "bot-1"
    board.canvas = Canvas(client, "c1")
    asyncio.run(board.bot.space.refresh_members())
    return client


def process(client, *messages):
    async def run():
        for msg in messages:
            await board.bot.process_message(msg)

    asyncio.run(run())


def test_board_empty_says_so():
    client = setup()
    process(client, message("!board"))
    assert client.sent[-1]["content"] == "the board is empty"


def test_add_places_a_note_and_acks():
    client = setup()
    client.respond("POST", "/channels/c1/canvas/objects", {"id": "o1", "seq": 5})
    process(client, message("!board add buy milk"))
    assert "added as #1: buy milk" in client.sent[-1]["content"]
    assert board.active_items(board.bot.db)[0][2] == "buy milk"


def test_add_refuses_text_past_the_length_cap():
    client = setup()
    process(client, message("!board add " + "x" * (board.MAX_TEXT_LENGTH + 1)))
    assert "max" in client.sent[-1]["content"]
    assert board.active_items(board.bot.db) == []


def test_add_refuses_when_the_board_is_full():
    client = setup()
    for i in range(board.MAX_SLOTS):
        board.bot.db.execute(
            "INSERT INTO items (id, slot, text, seq, active, added_by) VALUES (?, ?, ?, ?, 1, ?)", (f"o{i}", i, "x", i, "u1")
        )
    board.bot.db.commit()
    process(client, message("!board add one more"))
    assert "board is full" in client.sent[-1]["content"]


def test_done_removes_an_item():
    client = setup()
    client.respond("POST", "/channels/c1/canvas/objects", {"id": "o1", "seq": 5})
    client.respond("POST", "/channels/c1/canvas/ops", None)
    process(client, message("!board add buy milk", "m1"))
    process(client, message("!board done 1", "m2"))
    assert "done: buy milk" in client.sent[-1]["content"]
    assert board.active_items(board.bot.db) == []


def test_done_out_of_range_says_so():
    client = setup()
    process(client, message("!board done 5"))
    assert "no item #5" in client.sent[-1]["content"]


def test_move_relocates_an_item():
    client = setup()
    client.respond("POST", "/channels/c1/canvas/objects", {"id": "o1", "seq": 5})
    client.respond("POST", "/channels/c1/canvas/ops", None)
    process(client, message("!board add buy milk", "m1"))
    process(client, message("!board move 1 3", "m2"))
    assert "moved #1 to #3" in client.sent[-1]["content"]
    assert board.active_items(board.bot.db)[0][1] == 2  # 0-indexed slot


def test_move_refuses_a_taken_slot():
    client = setup()
    board.bot.db.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 1, 1, 'u1')")
    board.bot.db.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o2', 1, 'b', 2, 1, 'u1')")
    board.bot.db.commit()
    process(client, message("!board move 1 2"))
    assert "already taken" in client.sent[-1]["content"]


def test_clear_without_confirmation_asks_for_one():
    client = setup()
    board.bot.db.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 1, 1, 'u1')")
    board.bot.db.commit()
    process(client, message("!board clear"))
    assert "resend as `!board clear yes`" in client.sent[-1]["content"]
    assert len(board.active_items(board.bot.db)) == 1


def test_clear_yes_removes_everything():
    client = setup()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    board.bot.db.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 1, 1, 'u1')")
    board.bot.db.commit()
    process(client, message("!board clear yes"))
    assert "cleared 1 item(s)" in client.sent[-1]["content"]
    assert board.active_items(board.bot.db) == []


def test_unrecognised_board_command_shows_help():
    client = setup()
    process(client, message("!board nonsense"))
    assert "commands:" in client.sent[-1]["content"]


def test_canvas_objects_removed_event_deactivates_the_item():
    setup()
    board.bot.db.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 1, 1, 'u1')")
    board.bot.db.commit()
    asyncio.run(board.on_canvas_objects_removed({"object_ids": ["o1"]}))
    assert board.active_items(board.bot.db) == []


def test_canvas_cleared_event_deactivates_older_items():
    setup()
    board.bot.db.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 5, 1, 'u1')")
    board.bot.db.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o2', 1, 'b', 20, 1, 'u1')")
    board.bot.db.commit()
    asyncio.run(board.on_canvas_cleared({"before_seq": 10}))
    remaining = [row[0] for row in board.active_items(board.bot.db)]
    assert remaining == ["o2"]


def test_another_bot_is_ignored_by_default():
    client = setup()
    client.respond(
        "GET", "/members",
        MEMBERS + [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []}],
    )
    asyncio.run(board.bot.space.refresh_members())
    process(client, {"id": "m1", "author_id": "bot-2", "channel_id": "c1", "content": "!board add spam"})
    assert client.sent == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
