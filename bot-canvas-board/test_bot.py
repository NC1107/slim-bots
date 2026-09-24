#!/usr/bin/env python3
"""Command-and-event tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as board  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots import Canvas  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]


def message(content, msg_id="m1"):
    return {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content}


def frame(content, msg_id="m1"):
    """A full message.created envelope - needed for anything going through ctx.confirm's on_raw_message wait."""
    return {"type": "message.created", "channel_id": "c1", "message": message(content, msg_id)}


def setup():
    board.bot.store = Store(":memory:", migrate=board.init_db)
    asyncio.run(board.bot.store.open())
    board.bot.channels = {"c1"}
    board._names.clear()
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    board.bot.client = client
    board.bot.space = Space(client)
    board.bot.authors = AuthorFilter(client, space=board.bot.space, ignore_bots=True)
    board.bot.me_id = "bot-1"
    board.canvas = Canvas(client, "c1")
    board.canvas_channel_name = "voice-room"
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
    assert client.sent[-1]["content"] == "the board is empty (drawing on #voice-room)"


def test_add_places_a_note_and_acks():
    client = setup()
    client.respond("POST", "/channels/c1/canvas/objects", {"id": "o1", "seq": 5})
    process(client, message("!board add buy milk"))
    assert "added as #1: buy milk" in client.sent[-1]["content"]
    assert board.active_items(board.bot.store.connection)[0][2] == "buy milk"


def test_add_refuses_text_past_the_length_cap():
    client = setup()
    process(client, message("!board add " + "x" * (board.MAX_TEXT_LENGTH + 1)))
    assert "max" in client.sent[-1]["content"]
    assert board.active_items(board.bot.store.connection) == []


def test_add_refuses_when_the_board_is_full():
    client = setup()
    for i in range(board.MAX_SLOTS):
        board.bot.store.connection.execute(
            "INSERT INTO items (id, slot, text, seq, active, added_by) VALUES (?, ?, ?, ?, 1, ?)", (f"o{i}", i, "x", i, "u1")
        )
    board.bot.store.connection.commit()
    process(client, message("!board add one more"))
    assert "board is full" in client.sent[-1]["content"]


def test_done_removes_an_item():
    client = setup()
    client.respond("POST", "/channels/c1/canvas/objects", {"id": "o1", "seq": 5})
    client.respond("POST", "/channels/c1/canvas/ops", None)
    process(client, message("!board add buy milk", "m1"))
    process(client, message("!board done 1", "m2"))
    assert "done: buy milk" in client.sent[-1]["content"]
    assert board.active_items(board.bot.store.connection) == []


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
    assert board.active_items(board.bot.store.connection)[0][1] == 2  # 0-indexed slot


def test_move_refuses_a_taken_slot():
    client = setup()
    board.bot.store.connection.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 1, 1, 'u1')")
    board.bot.store.connection.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o2', 1, 'b', 2, 1, 'u1')")
    board.bot.store.connection.commit()
    process(client, message("!board move 1 2"))
    assert "already taken" in client.sent[-1]["content"]


def test_clear_asks_for_confirmation_and_a_no_reply_cancels():
    client = setup()
    board.bot.store.connection.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 1, 1, 'u1')")
    board.bot.store.connection.commit()

    async def run():
        clear_task = asyncio.ensure_future(board.bot._handle_frame(frame("!board clear", "m1")))
        await asyncio.sleep(0.01)
        await board.bot._handle_frame(frame("no", "m2"))
        await clear_task

    asyncio.run(run())
    assert "This removes all 1 item(s)" in client.sent[-2]["content"]
    assert client.sent[-1]["content"] == "cancelled"
    assert len(board.active_items(board.bot.store.connection)) == 1


def test_clear_yes_reply_removes_everything():
    client = setup()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    board.bot.store.connection.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 1, 1, 'u1')")
    board.bot.store.connection.commit()

    async def run():
        clear_task = asyncio.ensure_future(board.bot._handle_frame(frame("!board clear", "m1")))
        await asyncio.sleep(0.01)
        await board.bot._handle_frame(frame("yes", "m2"))
        await clear_task

    asyncio.run(run())
    assert "cleared 1 item(s)" in client.sent[-1]["content"]
    assert board.active_items(board.bot.store.connection) == []


def test_unrecognised_board_command_shows_help():
    client = setup()
    process(client, message("!board nonsense"))
    assert "commands:" in client.sent[-1]["content"]


def test_canvas_objects_removed_event_deactivates_the_item():
    setup()
    board.bot.store.connection.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 1, 1, 'u1')")
    board.bot.store.connection.commit()
    asyncio.run(board.on_canvas_objects_removed({"object_ids": ["o1"]}))
    assert board.active_items(board.bot.store.connection) == []


def test_canvas_cleared_event_deactivates_older_items():
    setup()
    board.bot.store.connection.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o1', 0, 'a', 5, 1, 'u1')")
    board.bot.store.connection.execute("INSERT INTO items (id, slot, text, seq, active, added_by) VALUES ('o2', 1, 'b', 20, 1, 'u1')")
    board.bot.store.connection.commit()
    asyncio.run(board.on_canvas_cleared({"before_seq": 10}))
    remaining = [row[0] for row in board.active_items(board.bot.store.connection)]
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
