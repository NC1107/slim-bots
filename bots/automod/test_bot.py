#!/usr/bin/env python3
"""Event-layer tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as automod  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

CHANNELS = [
    {"id": "c-chat", "name": "chat", "kind": "text", "category_id": "cat-1", "created_at": 0},
    {"id": "c-log", "name": "modlog", "kind": "text", "category_id": "cat-1", "created_at": 0},
]
USER = {"id": "u1", "username": "spammer", "display_name": "Spammer", "is_bot": False, "is_webhook": False}
MOD = {"id": "u-mod", "username": "mod", "display_name": "Mod", "is_bot": False, "is_webhook": False}
DEFAULTS = {name: getattr(automod, name) for name in (
    "FLOOD_MESSAGES", "MENTION_LIMIT", "LINK_POLICY", "LINK_DOMAINS", "NEW_MEMBER_HOURS", "WORDS", "WORD_CHANNELS", "LOG_CHANNEL",
)}
_counter = [0]


def setup(**config):
    for name, value in {**DEFAULTS, **config}.items():
        setattr(automod, name, value)
    automod._recent.clear()
    automod._log_channel_id = None
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/channels", CHANNELS)
    client.respond("GET", "/categories", [{"id": "cat-1", "name": "general"}])
    for user in (USER, MOD):
        client.respond("GET", f"/users/{user['id']}", user)
    client.respond("GET", "/users/u-mod", {**MOD, "role_ids": []})
    automod.bot.channels = None
    automod.bot.me_id = "bot-1"
    automod.bot.client = client
    automod.bot.space = Space(client)
    automod.bot.authors = AuthorFilter(client, space=automod.bot.space, ignore_bots=True)
    automod.bot.store = None
    automod.bot.data_path = os.path.join(tempfile.mkdtemp(), "automod.db")
    asyncio.run(automod.bot.space.refresh_channels())
    asyncio.run(automod.bot.open_store(migrate=automod.init_db))
    return client


def say(text, user_id="u1", channel="c-chat"):
    _counter[0] += 1
    frame = {"id": f"m{_counter[0]}", "channel_id": channel, "author_id": user_id, "content": text, "seq": _counter[0]}
    asyncio.run(automod.bot._handle_frame({"type": "message.created", "channel_id": channel, "message": frame}))
    return frame["id"]


def deleted(client):
    return [p for m, p, _, _ in client.calls if m == "DELETE" and "/messages/" in p]


def timed_out(client):
    return [p for m, p, _, _ in client.calls if m == "PUT" and p.endswith("/timeout")]


def logged(client):
    return [s["content"] for s in client.sent if s["channel_id"] == "c-log"]


def test_every_rule_is_off_by_default_so_nothing_is_touched():
    client = setup()
    for _ in range(30):
        say("hello https://evil.example @a @b @c @d @e @f damn")
    assert deleted(client) == []
    assert timed_out(client) == []
    assert client.sent == []


def test_flood_times_the_member_out_and_deletes_the_tripping_message():
    client = setup(FLOOD_MESSAGES=3, LOG_CHANNEL="general/modlog")
    for _ in range(3):
        say("hi")
    assert deleted(client) == []
    tripped = say("hi")
    assert deleted(client) == [f"/channels/c-chat/messages/{tripped}"]
    assert timed_out(client) == ["/members/u1/timeout"]
    assert any(c[2] == {"duration_seconds": 300, "reason": "automod: flood"} for c in client.calls if c[0] == "PUT")
    assert "[flood]" in logged(client)[0]
    assert "!automod lift" in logged(client)[0]


def test_mention_spam_deletes_and_times_out():
    client = setup(MENTION_LIMIT=2)
    say("@a @b")
    assert deleted(client) == []
    say("@a @b @c")
    assert len(deleted(client)) == 1
    assert timed_out(client) == ["/members/u1/timeout"]


def test_deny_list_blocks_a_domain_and_its_subdomains_but_not_others():
    client = setup(LINK_POLICY="deny", LINK_DOMAINS=["evil.example"])
    say("see https://www.evil.example/x?y=1")
    say("see (https://cdn.evil.example)")
    say("see https://good.example/evil.example")
    assert len(deleted(client)) == 2
    assert timed_out(client) == []
    assert any("removed by automod (link)" in s["content"] for s in client.sent)


def test_allow_list_blocks_every_link_that_is_not_listed():
    client = setup(LINK_POLICY="allow", LINK_DOMAINS=["good.example"])
    say("https://good.example/a and www.good.example")
    assert deleted(client) == []
    say("https://other.example")
    assert len(deleted(client)) == 1


def joined_ago(hours):
    return {**USER, "created_at": int((time.time() - hours * 3600) * 1000)}


def test_a_recent_joiner_cannot_post_links_but_an_old_member_can():
    client = setup(NEW_MEMBER_HOURS=24)
    client.respond("GET", "/users/u1", joined_ago(1))
    say("https://anything.example")
    assert len(deleted(client)) == 1
    say("no link here")
    assert len(deleted(client)) == 1
    client2 = setup(NEW_MEMBER_HOURS=24)
    client2.respond("GET", "/users/u1", joined_ago(48))
    say("https://anything.example")
    assert deleted(client2) == []


def test_new_member_status_comes_from_the_server_so_a_restart_keeps_it():
    client = setup(NEW_MEMBER_HOURS=24)
    client.respond("GET", "/users/u1", joined_ago(2))
    automod.bot.space.members.clear()
    say("https://anything.example")
    assert len(deleted(client)) == 1, "a member who joined before this process started is still new"


def test_a_member_without_a_join_time_is_not_treated_as_new():
    client = setup(NEW_MEMBER_HOURS=24)
    say("https://anything.example")
    assert deleted(client) == []


def test_word_list_matches_whole_words_only_and_can_be_limited_to_channels():
    client = setup(WORDS=["damn"], WORD_CHANNELS=["chat"])
    say("well, Damn!")
    say("damnation")
    assert len(deleted(client)) == 1
    client = setup(WORDS=["damn"], WORD_CHANNELS=["modlog"])
    say("damn")
    assert deleted(client) == []


def test_moderators_and_bots_are_exempt():
    client = setup(MENTION_LIMIT=1)
    member = automod.bot.space._make_member({**MOD, "role_ids": []})
    member._base_permissions = Permissions.MANAGE_MESSAGES
    automod.bot.space.members["u-mod"] = member
    say("@a @b @c", user_id="u-mod")
    client.respond("GET", "/users/u-bot", {**USER, "id": "u-bot", "is_bot": True})
    say("@a @b @c", user_id="u-bot")
    assert deleted(client) == []


def test_a_refused_action_is_logged_with_the_permission_hint_not_swallowed():
    client = setup(MENTION_LIMIT=1, LOG_CHANNEL="modlog")
    client.respond("PUT", "/members/u1/timeout", ApiError(403, "forbidden"))
    say("@a @b")
    text = logged(client)[0]
    assert "message deleted" in text
    assert "timed out 300s refused (403)" in text
    assert "KICK_MEMBERS" in text


def test_a_refused_delete_with_no_timeout_logs_that_no_action_was_taken():
    client = setup(LINK_POLICY="deny", LINK_DOMAINS=["evil.example"], LOG_CHANNEL="modlog")
    message_id = "m-refused"
    client.respond("DELETE", f"/channels/c-chat/messages/{message_id}", ApiError(403, "forbidden"))
    frame = {"id": message_id, "channel_id": "c-chat", "author_id": "u1", "content": "https://evil.example", "seq": 1}
    asyncio.run(automod.bot._handle_frame({"type": "message.created", "channel_id": "c-chat", "message": frame}))
    text = logged(client)[0]
    assert "no action taken" in text
    assert "message deleted refused (403)" in text


def test_no_log_channel_configured_means_no_log_post():
    client = setup(MENTION_LIMIT=1)
    say("@a @b")
    assert logged(client) == []


def test_lift_needs_kick_members_and_calls_the_route():
    client = setup()
    automod.bot.space.members["u1"] = automod.bot.space._make_member(USER)
    mod = automod.bot.space._make_member({**MOD, "role_ids": []})
    automod.bot.space.members["u-mod"] = mod
    msg = {"id": "cmd", "channel_id": "c-chat", "author_id": "u-mod", "content": "!automod lift @spammer", "seq": 1}
    asyncio.run(automod.bot.process_message(msg))
    assert "Kick Members" in client.sent[-1]["content"]
    assert timed_out(client) == []
    mod._base_permissions = Permissions.KICK_MEMBERS
    asyncio.run(automod.bot.process_message(msg))
    assert ("DELETE", "/members/u1/timeout") in [(m, p) for m, p, _, _ in client.calls]


def test_the_log_and_usage_hint_name_the_configured_prefix():
    client = setup(FLOOD_MESSAGES=3, LOG_CHANNEL="general/modlog")
    original, automod.bot.prefix = automod.bot.prefix, "?"
    try:
        for _ in range(4):
            say("hi")
        asyncio.run(automod.bot.process_message({"id": "cmd", "channel_id": "c-chat", "author_id": "u1", "content": "?automod nonsense", "seq": 1}))
    finally:
        automod.bot.prefix = original
    assert "`?automod lift " in logged(client)[0]
    assert "try `?automod rules`" in client.sent[-1]["content"]


def test_a_message_from_an_author_the_server_cannot_resolve_is_left_alone():
    client = setup(WORDS=["damn"])
    client.respond("GET", "/users/ghost", ApiError(404, "not found"))
    say("damn", user_id="ghost")
    assert deleted(client) == []
    say("damn")
    assert len(deleted(client)) == 1


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
