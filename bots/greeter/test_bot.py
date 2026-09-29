#!/usr/bin/env python3
"""Event-layer tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as greeter  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

NEWBIE = {"id": "u-new", "username": "newbie", "display_name": "Newbie", "is_bot": False, "is_webhook": False}
CHANNELS = [
    {"id": "c-chat", "name": "chat", "kind": "text", "category_id": "cat-general", "created_at": 0},
    {"id": "c-chat-dev", "name": "chat", "kind": "text", "category_id": "cat-dev", "created_at": 0},
    {"id": "c-backlog", "name": "backlog", "kind": "text", "category_id": "cat-dev", "created_at": 0},
]
CATEGORIES = [{"id": "cat-general", "name": "general"}, {"id": "cat-dev", "name": "dev"}]


def setup(pinned=None):
    """A fresh store per test, so `!welcome here` from one test never leaks into the next."""
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/channels", CHANNELS)
    client.respond("GET", "/categories", CATEGORIES)
    greeter.bot.channels = {pinned} if pinned else None
    greeter.bot.client = client
    greeter.bot.space = Space(client)
    greeter.bot.authors = AuthorFilter(client, space=greeter.bot.space, ignore_bots=True)
    greeter.bot.store = None
    greeter.bot.data_path = os.path.join(tempfile.mkdtemp(), "greeter.db")
    asyncio.run(greeter.bot.space.refresh_members())
    asyncio.run(greeter.bot.open_store(migrate=greeter.init_db))
    return client


def handle(frame):
    asyncio.run(greeter.bot._handle_frame(frame))


def join(client):
    client.respond("GET", "/users/u-new", NEWBIE)
    handle({"type": "member.joined", "user_id": "u-new"})


def command(client, text, *, author_permissions):
    author = {"id": "u-admin", "username": "admin", "display_name": "Admin", "is_bot": False, "is_webhook": False}
    client.respond("GET", "/users/u-admin", author)
    greeter.bot.space.members.pop("u-admin", None)
    member = greeter.bot.space._make_member({**author, "role_ids": []})
    member._base_permissions = author_permissions
    greeter.bot.space.members["u-admin"] = member
    asyncio.run(greeter.bot.process_message(
        {"id": "m1", "channel_id": "c-backlog", "author_id": "u-admin", "content": text, "seq": 1},
    ))


def test_by_default_the_welcome_goes_to_chat_in_general_not_a_chat_elsewhere():
    client = setup()
    join(client)
    assert client.sent[-1]["channel_id"] == "c-chat"
    assert "@newbie" in client.sent[-1]["content"]
    assert client.sent[-1]["embeds"][0]["description"] == client.sent[-1]["content"]


def test_an_admin_can_move_the_welcome_here_and_it_sticks():
    client = setup()
    command(client, "!welcome here", author_permissions=Permissions.MANAGE_SERVER)
    assert "welcomed here" in client.sent[-1]["content"]
    join(client)
    assert client.sent[-1]["channel_id"] == "c-backlog"
    greeter.bot.store = None
    asyncio.run(greeter.bot.open_store(migrate=greeter.init_db))
    join(client)
    assert client.sent[-1]["channel_id"] == "c-backlog", "the choice survives a restart"


def test_a_member_without_manage_server_cannot_move_it():
    client = setup()
    command(client, "!welcome here", author_permissions=0)
    assert "Manage Server" in client.ephemerals[-1]["content"]
    assert client.ephemerals[-1]["in_reply_to_id"] == "m1"
    assert not client.sent, "the refusal is private, so nothing is posted to the channel"
    join(client)
    assert client.sent[-1]["channel_id"] == "c-chat"


def test_slimm_channels_still_pins_the_channel_outright():
    client = setup(pinned="c-backlog")
    join(client)
    assert client.sent[-1]["channel_id"] == "c-backlog"


def test_no_matching_channel_posts_nothing_rather_than_guessing():
    client = setup()
    greeter.GREETER_CHANNEL = "nowhere/lobby"
    try:
        join(client)
        assert client.sent == []
    finally:
        greeter.GREETER_CHANNEL = "general/chat"


def test_a_custom_message_template_is_honoured():
    greeter.GREETER_MESSAGE = "Say hi to {member}!"
    try:
        client = setup()
        join(client)
        assert client.sent[-1]["content"] == "Say hi to @newbie!"
    finally:
        greeter.GREETER_MESSAGE = "Welcome, {member}! Make yourself at home."


def test_an_unresolvable_join_posts_nothing():
    from slimbots.http import ApiError

    client = setup()
    client.respond("GET", "/users/ghost", ApiError(404, "not found"))
    handle({"type": "member.joined", "user_id": "ghost"})
    assert client.sent == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
