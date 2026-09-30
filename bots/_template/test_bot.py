#!/usr/bin/env python3
"""Tests against FakeAsyncClient, no server needed; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as template  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

PERSON = {"id": "u-1", "username": "sam", "display_name": "Sam", "is_bot": False, "is_webhook": False}


def setup():
    """A fresh store and cooldown per test, so nothing leaks from one test into the next."""
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/users/u-1", PERSON)
    template.bot.client = client
    template.bot.space = Space(client)
    template.bot.authors = AuthorFilter(client, space=template.bot.space, ignore_bots=True)
    template.bot.store = None
    template.bot.commands["vote"].cooldown._last.clear()
    template.bot.data_path = os.path.join(tempfile.mkdtemp(), "template.db")
    asyncio.run(template.bot.space.refresh_members())
    asyncio.run(template.bot.open_store(migrate=template.init_db))
    return client


def say(client, text, *, permissions=0):
    member = template.bot.space._make_member({**PERSON, "role_ids": []})
    member._base_permissions = permissions
    template.bot.space.members["u-1"] = member
    asyncio.run(template.bot.process_message({"id": "m1", "channel_id": "c1", "author_id": "u-1", "content": text, "seq": 1}))


def press(custom_id):
    frame = {
        "type": "interaction.created", "interaction_id": "i1", "channel_id": "c1", "message_id": "m-poll",
        "custom_id": custom_id, "user_id": "u-1", "user_display_name": "Sam", "created_at": 1,
    }

    async def run():
        await template.bot._handle_frame(frame)
        await asyncio.gather(*list(template.bot._background_tasks))

    asyncio.run(run())


def test_vote_posts_the_question_with_two_buttons():
    client = setup()
    say(client, "!vote pizza friday?")
    sent = client.sent[-1]
    assert sent["content"] == "pizza friday?"
    assert [b["custom_id"] for b in sent["components"][0]["buttons"]] == ["vote:yes", "vote:no"]


def test_an_overlong_question_is_refused_privately():
    client = setup()
    say(client, "!vote " + "x" * 300)
    assert "under 200" in client.ephemerals[-1]["content"]
    assert not client.sent


def test_a_press_is_counted_once_per_member_and_can_change():
    client = setup()
    press("vote:yes")
    assert "counted: yes" in client.ephemerals[-1]["content"]
    press("vote:no")
    say(client, "!tally m-poll", permissions=Permissions.MANAGE_MESSAGES)
    assert client.sent[-1]["content"] == "yes: 0, no: 1"


def test_a_tally_needs_manage_messages():
    client = setup()
    say(client, "!tally m-poll")
    assert "Manage Messages" in client.ephemerals[-1]["content"]
    assert not client.sent


def test_a_press_with_an_unknown_choice_is_acked_not_counted():
    client = setup()
    press("vote:maybe")
    assert client.acks == ["i1"]
    assert not client.ephemerals


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
