#!/usr/bin/env python3
"""Unit tests for bot.py's command handling, using slimbots.testing.FakeClient
so none of this needs a live deployment or a socket.

Run it directly, no test framework needed:

    pip install -r requirements.txt
    python3 test_bot.py
"""

import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402
from slimbots import AuthorFilter  # noqa: E402
from slimbots.testing import FakeClient  # noqa: E402

ME = "bot-1"
USER = "user-1"
OTHER_BOT = "bot-2"
MEMBER_ROLE = "role-member"
HELPER_ROLE = "role-helper"

bot.CHANNEL = "chan-1"
bot.ROLES = {"member": MEMBER_ROLE, "helper": HELPER_ROLE}

MANAGE_ROLES = bot.PERMISSION_BITS["MANAGE_ROLES"]
MANAGE_MESSAGES = bot.PERMISSION_BITS["MANAGE_MESSAGES"]


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def fresh():
    bot._role_permissions_cache.clear()
    client = FakeClient(me_id=ME)
    client.respond("GET", f"/users/{USER}", {"is_bot": False, "role_ids": []})
    client.respond("GET", f"/users/{OTHER_BOT}", {"is_bot": True, "role_ids": []})
    return client, AuthorFilter(client)


def incoming(content, author=USER, message_id="m1"):
    return {"author_id": author, "content": content, "id": message_id}


def test_ignores_its_own_messages():
    client, authors = fresh()
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!role member", author=ME))
    assert client.sent == []


def test_ignores_another_bot():
    client, authors = fresh()
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!role member", author=OTHER_BOT))
    assert client.sent == []


def test_unknown_role_is_refused_by_name():
    client, authors = fresh()
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!role nonexistent"))
    assert "no role called" in client.sent[0]["content"]


def test_grant_succeeds_when_the_bot_holds_enough():
    client, authors = fresh()
    client.respond("PUT", f"/members/{USER}/roles/{MEMBER_ROLE}", {})
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!role member"))
    assert client.sent[0]["content"] == "done - you have `member` now."


def test_revoke_succeeds():
    client, authors = fresh()
    client.respond("DELETE", f"/members/{USER}/roles/{MEMBER_ROLE}", {})
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!role remove member"))
    assert client.sent[0]["content"] == "removed `member`."


def test_grant_403_without_manage_roles_names_that_gap():
    client, authors = fresh()
    client.respond("PUT", f"/members/{USER}/roles/{MEMBER_ROLE}", lambda: (_ for _ in ()).throw(http_error(403)))
    bot.handle_message(client, ME, 0, authors, incoming("!role member"))
    assert "MANAGE_ROLES" in client.sent[0]["content"]
    assert "don't hold MANAGE_ROLES myself" in client.sent[0]["content"]


def test_grant_403_with_manage_roles_names_the_missing_bits():
    client, authors = fresh()
    client.respond("PUT", f"/members/{USER}/roles/{HELPER_ROLE}", lambda: (_ for _ in ()).throw(http_error(403)))
    client.respond(
        "GET",
        "/roles",
        [
            {"id": MEMBER_ROLE, "name": "member", "permissions": 0},
            {"id": HELPER_ROLE, "name": "helper", "permissions": MANAGE_MESSAGES},
        ],
    )
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!role helper"))
    reply = client.sent[0]["content"]
    assert "MANAGE_MESSAGES" in reply
    assert "don't hold MANAGE_ROLES myself" not in reply


def test_mine_lists_only_configured_roles_held():
    client, authors = fresh()
    client.respond("GET", f"/users/{USER}", {"is_bot": False, "role_ids": [MEMBER_ROLE, "role-unrelated"]})
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!role mine"))
    assert client.sent[0]["content"] == "you hold: member"


def test_mine_says_so_when_nothing_is_held():
    client, authors = fresh()
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!role mine"))
    assert "none of the roles" in client.sent[0]["content"]


def test_status_reports_missing_manage_roles():
    client, authors = fresh()
    bot.handle_message(client, ME, 0, authors, incoming("!roles status"))
    assert "MANAGE_ROLES is missing" in client.sent[0]["content"]


def test_status_reports_per_role_grantability():
    client, authors = fresh()
    client.respond(
        "GET",
        "/roles",
        [
            {"id": MEMBER_ROLE, "name": "member", "permissions": 0},
            {"id": HELPER_ROLE, "name": "helper", "permissions": MANAGE_MESSAGES},
        ],
    )
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!roles status"))
    reply = client.sent[0]["content"]
    assert "`member`: grantable" in reply
    assert "`helper`: missing MANAGE_MESSAGES" in reply


def test_roles_listing_mentions_mine():
    client, authors = fresh()
    bot.handle_message(client, ME, MANAGE_ROLES, authors, incoming("!roles"))
    assert "!role mine" in client.sent[0]["content"]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
