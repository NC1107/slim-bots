#!/usr/bin/env python3
"""Event-layer tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as greeter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

NEWBIE = {"id": "u-new", "username": "newbie", "display_name": "Newbie", "is_bot": False, "is_webhook": False}


def setup():
    client = FakeAsyncClient(me_id="bot-1")
    greeter.bot.channels = {"c1"}
    greeter.bot.client = client
    greeter.bot.space = Space(client)
    asyncio.run(greeter.bot.space.refresh_members())
    return client


def handle(client, frame):
    asyncio.run(greeter.bot._handle_frame(frame))


def test_a_join_posts_the_default_welcome_with_a_mention():
    client = setup()
    client.respond("GET", "/users/u-new", NEWBIE)
    handle(client, {"type": "member.joined", "user_id": "u-new"})
    assert len(client.sent) == 1
    assert "@newbie" in client.sent[-1]["content"]
    assert client.sent[-1]["embeds"][0]["description"] == client.sent[-1]["content"]


def test_a_custom_message_template_is_honoured():
    greeter.GREETER_MESSAGE = "Say hi to {member}!"
    try:
        client = setup()
        client.respond("GET", "/users/u-new", NEWBIE)
        handle(client, {"type": "member.joined", "user_id": "u-new"})
        assert client.sent[-1]["content"] == "Say hi to @newbie!"
    finally:
        greeter.GREETER_MESSAGE = "Welcome, {member}! Make yourself at home."


def test_an_unresolvable_join_posts_nothing():
    from slimbots.http import ApiError

    client = setup()
    client.respond("GET", "/users/ghost", ApiError(404, "not found"))
    handle(client, {"type": "member.joined", "user_id": "ghost"})
    assert client.sent == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
