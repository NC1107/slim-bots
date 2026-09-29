#!/usr/bin/env python3
"""Event tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import time

os.environ["STARBOARD_CHANNEL"] = "hl"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as starboard  # noqa: E402
from slimbots import Message, Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.events import ReactionsChanged  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402


def member(user_id, name, **flags):
    return {"id": user_id, "username": name, "display_name": name.title(), "is_bot": False, "is_webhook": False, "role_ids": [], "roles": [], **flags}


MEMBERS = [member("u1", "nick"), member("b1", "helper", is_bot=True), member("w1", "hook", is_webhook=True)]
CHANNELS = [
    {"id": "c1", "name": "general", "kind": "text", "restricted": False},
    {"id": "hl", "name": "highlights", "kind": "text", "restricted": False},
    {"id": "priv", "name": "staff", "kind": "text", "restricted": True},
]
STAR = "⭐"


def setup():
    starboard.bot.store = Store(":memory:", migrate=starboard.init_db)
    asyncio.run(starboard.bot.store.open())
    starboard.bot.channels = {"c1", "priv"}
    starboard._locks.clear()
    client = FakeAsyncClient(me_id="bot-1", base="https://slim.example")
    client.respond("GET", "/members", MEMBERS)
    client.respond("GET", "/channels", CHANNELS)
    starboard.bot.client = client
    starboard.bot.space = Space(client)
    starboard.bot.authors = AuthorFilter(client, space=starboard.bot.space, ignore_bots=True)
    starboard.bot.me_id = "bot-1"
    asyncio.run(starboard.bot.space.refresh_members())
    asyncio.run(starboard.bot.space.refresh_channels())
    return client


def frame(kind, **fields):
    asyncio.run(starboard.bot._handle_frame({"type": kind, **fields}))


def created(msg_id="m1", content="hello world", channel_id="c1", author_id="u1", **extra):
    frame("message.created", channel_id=channel_id, message={"id": msg_id, "author_id": author_id, "channel_id": channel_id, "content": content, "seq": 1, **extra})


def react(count, msg_id="m1", channel_id="c1", emoji=STAR):
    frame("reactions.changed", channel_id=channel_id, message_id=msg_id, reactions=[{"emoji": emoji, "count": count}])


def db(sql, *args):
    async def run():
        return await starboard.bot.store.run(lambda conn: conn.execute(sql, args).fetchall())

    return asyncio.run(run())


def test_below_threshold_posts_nothing():
    client = setup()
    created()
    react(2)
    assert client.sent == []


def test_reaching_threshold_posts_a_highlight_with_a_link():
    client = setup()
    created(content="a good one")
    react(3)
    assert len(client.sent) == 1
    sent = client.sent[0]
    assert sent["channel_id"] == "hl"
    assert "> a good one" in sent["content"]
    assert "Nick in #general" in sent["content"]
    assert "https://slim.example/channels/c1/m/m1" in sent["content"]
    assert sent["content"].startswith(f"{STAR} 3")


def test_never_posted_twice():
    client = setup()
    created()
    react(3)
    react(3)
    react(3)
    assert len(client.sent) == 1
    assert len(db("SELECT * FROM starred")) == 1


def test_a_racing_pair_of_events_posts_once():
    client = setup()
    created()

    async def both():
        event = ReactionsChanged({"channel_id": "c1", "message_id": "m1", "reactions": [{"emoji": STAR, "count": 3}]})
        await asyncio.gather(starboard.on_reactions_changed(event), starboard.on_reactions_changed(event))

    asyncio.run(both())
    assert len(client.sent) == 1


def test_count_change_edits_the_highlight():
    client = setup()
    created()
    react(3)
    hl_id = client.sent[0]["id"]
    client.respond("PATCH", f"/channels/hl/messages/{hl_id}", {})
    react(5)
    patches = [c for c in client.calls if c[0] == "PATCH"]
    assert len(patches) == 1
    assert patches[0][2]["content"].startswith(f"{STAR} 5")
    assert len(client.sent) == 1


def test_only_the_configured_emoji_counts():
    client = setup()
    created()
    react(9, emoji="\U0001f44d")
    assert client.sent == []


def test_variation_selector_still_matches():
    client = setup()
    created()
    react(3, emoji=STAR + "️")
    assert len(client.sent) == 1


def test_an_unseen_message_is_fetched_by_id_and_mirrored():
    client = setup()
    client.respond("GET", "/channels/c1/messages/late", {"id": "late", "author_id": "u1", "content": "from before", "attachments": []})
    react(4, msg_id="late")
    assert [c[1] for c in client.calls if c[0] == "GET" and "/messages/" in c[1]] == ["/channels/c1/messages/late"]
    assert len(client.sent) == 1
    assert "> from before" in client.sent[0]["content"]
    assert len(db("SELECT * FROM starred")) == 1


def test_an_unseen_message_that_cannot_be_fetched_is_skipped():
    client = setup()
    client.respond("GET", "/channels/c1/messages/gone", ApiError(404, {"error": "message not found"}))
    react(4, msg_id="gone")
    assert client.sent == []
    assert db("SELECT * FROM starred") == []


def test_a_fetched_message_is_fetched_once():
    client = setup()
    client.respond("GET", "/channels/c1/messages/late", {"id": "late", "author_id": "u1", "content": "x"})
    client.respond("PATCH", f"/channels/hl/messages/{starboard.highlight_id('late')}", {})
    react(4, msg_id="late")
    react(5, msg_id="late")
    assert len([c for c in client.calls if c[0] == "GET" and "/messages/" in c[1]]) == 1


def test_a_restricted_origin_is_not_mirrored_into_a_public_starboard():
    client = setup()
    created(channel_id="priv")
    react(5, channel_id="priv")
    assert client.sent == []
    assert db("SELECT * FROM starred") == []


def test_a_restricted_origin_is_mirrored_into_a_restricted_starboard():
    client = setup()
    starboard.bot.space.channels["hl"].restricted = True
    created(channel_id="priv")
    react(5, channel_id="priv")
    assert len(client.sent) == 1


def test_an_origin_of_unknown_visibility_fails_closed():
    client = setup()
    starboard.bot.space.channels["c1"].restricted = None
    created()
    react(3)
    assert client.sent == []


def test_a_bot_message_is_never_mirrored():
    client = setup()
    created(author_id="b1")
    client.respond("GET", "/channels/c1/messages/m1", {"id": "m1", "author_id": "b1", "content": "x"})
    react(9)
    assert client.sent == []
    assert db("SELECT * FROM seen") == []


def test_a_webhook_message_is_never_mirrored():
    client = setup()
    created(author_id="w1")
    client.respond("GET", "/channels/c1/messages/m1", {"id": "m1", "author_id": "w1", "content": "x"})
    react(9)
    assert client.sent == []


def test_a_fetched_bot_message_is_never_mirrored():
    client = setup()
    client.respond("GET", "/channels/c1/messages/late", {"id": "late", "author_id": "b1", "content": "beep"})
    react(9, msg_id="late")
    assert client.sent == []
    assert db("SELECT * FROM seen") == []


def test_original_edit_updates_the_highlight():
    client = setup()
    created(content="before")
    react(3)
    hl_id = client.sent[0]["id"]
    client.respond("PATCH", f"/channels/hl/messages/{hl_id}", {})
    frame("message.edited", channel_id="c1", seq=2, message={"id": "m1", "content": "after"})
    patch = [c for c in client.calls if c[0] == "PATCH"][-1]
    assert "> after" in patch[2]["content"]
    assert db("SELECT content FROM starred") == [("after",)]


def test_original_delete_removes_the_highlight():
    client = setup()
    created()
    react(3)
    hl_id = client.sent[0]["id"]
    client.respond("DELETE", f"/channels/hl/messages/{hl_id}", None)
    frame("message.deleted", channel_id="c1", message_id="m1")
    assert [c for c in client.calls if c[0] == "DELETE"]
    assert db("SELECT * FROM starred") == []
    assert db("SELECT * FROM seen") == []


def test_the_highlight_channel_is_never_a_source():
    client = setup()
    starboard.bot.channels = {"c1", "hl"}
    created(channel_id="hl")
    react(9, channel_id="hl")
    assert client.sent == []
    assert db("SELECT * FROM seen") == []


def test_attachments_ride_along_by_id():
    client = setup()
    created(content="", attachments=[{"id": "a"}, {"id": "b"}])
    react(3)
    assert client.sent[0]["attachment_ids"] == ["a", "b"]
    assert "attachment" not in client.sent[0]["content"]


def test_a_fetched_message_carries_its_attachments_too():
    client = setup()
    client.respond("GET", "/channels/c1/messages/late", {"id": "late", "author_id": "u1", "content": "", "attachments": [{"id": "z"}]})
    react(3, msg_id="late")
    assert client.sent[0]["attachment_ids"] == ["z"]


def test_a_rejected_attachment_send_falls_back_to_text():
    client = setup()
    created(content="pic", attachments=[{"id": "a"}])
    real_call = client.call
    calls = []

    async def call(method, path, body=None, **kw):
        if method == "POST" and body and body.get("attachment_ids"):
            calls.append(body)
            raise ApiError(403, {"error": "missing ATTACH_FILES"})
        return await real_call(method, path, body, **kw)

    client.call = call
    react(3)
    assert len(calls) == 1
    assert len(client.sent) == 1
    assert "attachment_ids" not in client.sent[0]


def test_an_old_row_without_ids_still_notes_its_attachments():
    body = starboard.render_highlight("m1", "c1", "u1", "hi", 2, "", 3)
    assert "(+2 attachments not shown)" in body


def test_the_link_is_the_per_message_route():
    client = setup()
    created()
    react(3)
    assert "https://slim.example/channels/c1/m/m1" in client.sent[0]["content"]


def test_an_existing_database_gains_the_attachment_ids_column():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE seen (message_id TEXT PRIMARY KEY, channel_id TEXT, author_id TEXT, content TEXT, attachments INTEGER, seen_at INTEGER)")
    conn.execute("CREATE TABLE starred (message_id TEXT PRIMARY KEY, channel_id TEXT, author_id TEXT, content TEXT, attachments INTEGER, highlight_id TEXT, count INTEGER, starred_at INTEGER)")
    starboard.init_db(conn)
    for table in ("seen", "starred"):
        assert "attachment_ids" in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_highlight_id_is_stable_for_idempotent_retries():
    first, again = starboard.highlight_id("m1"), starboard.highlight_id("m1")
    assert first == again
    assert starboard.highlight_id("m1") != starboard.highlight_id("m2")


def test_digest_first_run_only_starts_the_clock():
    client = setup()
    asyncio.run(starboard.post_digest_if_due(1000))
    assert client.sent == []
    assert db("SELECT value FROM meta WHERE key = 'last_digest'") == [("1000",)]


def test_digest_posts_top_highlights_once_due():
    client = setup()
    created("m1", "first")
    created("m2", "second")
    react(3, "m1")
    react(7, "m2")
    client.sent.clear()
    asyncio.run(starboard.bot.store.run(starboard.meta_set, "last_digest", int(time.time()) - 8 * 86400))
    asyncio.run(starboard.post_digest_if_due(int(time.time())))
    assert len(client.sent) == 1
    body = client.sent[0]["content"]
    assert body.index("second") < body.index("first")


def test_digest_not_due_before_the_interval():
    client = setup()
    created()
    react(3)
    client.sent.clear()
    asyncio.run(starboard.bot.store.run(starboard.meta_set, "last_digest", int(time.time()) - 86400))
    asyncio.run(starboard.post_digest_if_due(int(time.time())))
    assert client.sent == []


def test_digest_with_nothing_starred_posts_nothing_and_resets_the_clock():
    client = setup()
    asyncio.run(starboard.bot.store.run(starboard.meta_set, "last_digest", 0))
    asyncio.run(starboard.post_digest_if_due(10 * 86400))
    assert client.sent == []
    assert db("SELECT value FROM meta WHERE key = 'last_digest'") == [(str(10 * 86400),)]


def test_prune_forgets_old_seen_but_keeps_starred():
    setup()
    created()
    react(3)
    created("m2", "old")
    asyncio.run(starboard.bot.store.run(lambda conn: conn.execute("UPDATE seen SET seen_at = 0")))
    asyncio.run(starboard.bot.store.run(starboard.prune_seen, 100))
    assert db("SELECT * FROM seen") == []
    assert len(db("SELECT * FROM starred")) == 1


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
