#!/usr/bin/env python3
"""Event tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import time

os.environ["STARBOARD_CHANNEL"] = "hl"
os.environ["SLIMM_URL"] = "https://slim.example"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as starboard  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.events import ReactionsChanged  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": [], "roles": []}]
CHANNELS = [{"id": "c1", "name": "general", "kind": "text"}, {"id": "hl", "name": "highlights", "kind": "text"}]
STAR = "⭐"


def setup():
    starboard.bot.store = Store(":memory:", migrate=starboard.init_db)
    asyncio.run(starboard.bot.store.open())
    starboard.bot.channels = {"c1"}
    starboard._locks.clear()
    client = FakeAsyncClient(me_id="bot-1")
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


def created(msg_id="m1", content="hello world", channel_id="c1", **extra):
    frame("message.created", channel_id=channel_id, message={"id": msg_id, "author_id": "u1", "channel_id": channel_id, "content": content, "seq": 1, **extra})


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
    assert "https://slim.example/channels/c1" in sent["content"]
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


def test_unseen_message_is_not_mirrored():
    client = setup()
    react(4, msg_id="never-seen")
    assert client.sent == []
    assert db("SELECT * FROM starred") == []


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


def test_attachments_are_counted_in_the_body():
    client = setup()
    created(content="", attachments=[{"id": "a"}, {"id": "b"}])
    react(3)
    assert "(+2 attachments)" in client.sent[0]["content"]


def test_highlight_id_is_stable_for_idempotent_retries():
    assert starboard.highlight_id("m1") == starboard.highlight_id("m1")
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
