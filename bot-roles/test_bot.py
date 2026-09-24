#!/usr/bin/env python3
"""Command-layer tests against slimbots.testing.FakeAsyncClient.

Run it directly, no test framework needed: python3 test_bot.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SLIMM_CHANNEL", "c1")
os.environ.setdefault("SLIMM_ROLES", "member:r-member,helper:r-helper")

import bot as roles  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]
ROLE_DEFS = [
    {"id": "r-everyone", "name": "@everyone", "permissions": 0, "is_everyone": True},
    {"id": "r-member", "name": "member", "permissions": 0, "is_everyone": False},
    {"id": "r-helper", "name": "helper", "permissions": 8, "is_everyone": False},  # MANAGE_MESSAGES
]


def message(author_id, content, msg_id="m1"):
    return {"id": msg_id, "author_id": author_id, "channel_id": "c1", "content": content}


def setup(*, my_permissions=32):  # MANAGE_ROLES
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    client.respond("GET", "/roles", ROLE_DEFS)
    client.respond("GET", "/me", {"id": "bot-1", "permissions": my_permissions})
    client.respond("PATCH", f"/channels/c1/messages/{roles.listing_message_id()}", None)
    roles.bot.client = client
    roles.bot.space = Space(client)
    roles.bot.authors = AuthorFilter(client, space=roles.bot.space, ignore_bots=True)
    roles.bot.me_id = "bot-1"
    roles._role_permissions_cache.clear()
    roles._last_seq.clear()
    asyncio.run(roles.bot.space.refresh_members())
    roles.bot.my_permissions = my_permissions
    return client


def process(client, *messages):
    async def run():
        for msg in messages:
            await roles.bot.process_message(msg)

    asyncio.run(run())


def test_roles_lists_the_offer():
    client = setup()
    process(client, message("u1", "!roles"))
    assert "member" in client.sent[-1]["content"]
    assert "helper" in client.sent[-1]["content"]


def test_role_grants_a_role_the_bot_can_hand_out():
    client = setup()
    client.respond("PUT", "/members/u1/roles/r-member", None)
    process(client, message("u1", "!role member"))
    assert "you have `member` now" in client.sent[-1]["content"]


def test_role_refuses_an_unlisted_name():
    client = setup()
    process(client, message("u1", "!role admin"))
    assert "no role called" in client.sent[-1]["content"]


def test_role_remove_revokes():
    client = setup()
    client.respond("DELETE", "/members/u1/roles/r-member", None)
    process(client, message("u1", "!role remove member"))
    assert "removed `member`" in client.sent[-1]["content"]


def test_role_mine_lists_held_roles():
    client = setup()
    roles.bot.space.members["u1"].role_ids = ["r-member"]
    process(client, message("u1", "!role mine"))
    assert "member" in client.sent[-1]["content"]


def test_role_mine_says_none_when_empty():
    client = setup()
    process(client, message("u1", "!role mine"))
    assert "none of the roles" in client.sent[-1]["content"]


def test_escalation_names_the_missing_permission_when_bot_lacks_manage_roles():
    from slimbots.http import ApiError

    client = setup(my_permissions=0)
    client.respond("PUT", "/members/u1/roles/r-helper", ApiError(403, {"error": "forbidden"}))
    process(client, message("u1", "!role helper"))
    assert "MANAGE_ROLES" in client.sent[-1]["content"]


def test_escalation_names_the_missing_permission_when_role_carries_more():
    from slimbots.http import ApiError

    client = setup(my_permissions=32)  # MANAGE_ROLES only, not MANAGE_MESSAGES
    client.respond("PUT", "/members/u1/roles/r-helper", ApiError(403, {"error": "forbidden"}))
    process(client, message("u1", "!role helper"))
    assert "MANAGE_MESSAGES" in client.sent[-1]["content"]


def test_roles_status_reports_grantable_and_missing():
    client = setup(my_permissions=32)
    process(client, message("u1", "!roles status"))
    reply = client.sent[-1]["content"]
    assert "`member`: grantable" in reply
    assert "`helper`: missing MANAGE_MESSAGES" in reply


def test_another_bot_is_ignored_by_default():
    client = setup()
    client.respond(
        "GET",
        "/members",
        MEMBERS + [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []}],
    )
    asyncio.run(roles.bot.space.refresh_members())
    process(client, message("bot-2", "!role member"))
    assert client.sent == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
