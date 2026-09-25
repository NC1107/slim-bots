#!/usr/bin/env python3
"""Event-driven tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as starboard  # noqa: E402
from slimbots import ApiError, Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [
    {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []},
    {"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []},
]


def origin_message(message_id="m1", author_id="u1", content="hi", attachments=None):
    return {
        "id": message_id, "channel_id": "c1", "author_id": author_id, "author_display_name": "Nick",
        "content": content, "attachments": attachments or [],
    }


def setup(*, origin_restricted=False, starboard_restricted=False):
    starboard.bot.store = Store(":memory:", migrate=starboard.init_db)
    asyncio.run(starboard.bot.store.open())
    starboard.bot.channels = {"c1"}
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    starboard.bot.client = client
    starboard.bot.space = Space(client)
    starboard.bot.space.channels = {
        "c1": Channel({"id": "c1", "name": "general", "restricted": origin_restricted}),
        "sb": Channel({"id": "sb", "name": "highlights", "restricted": starboard_restricted}),
    }
    starboard.bot.authors = AuthorFilter(client, space=starboard.bot.space, ignore_bots=True)
    starboard.bot.me_id = "bot-1"
    starboard.starboard_channel = starboard.bot.space.get_channel("sb")
    asyncio.run(starboard.bot.space.refresh_members())
    return client


def reactions_frame(count, message_id="m1", channel_id="c1"):
    return {
        "type": "reactions.changed", "channel_id": channel_id, "message_id": message_id,
        "reactions": [{"emoji": starboard.STARBOARD_EMOJI, "count": count, "reacted": False}],
    }


def handle(frame):
    asyncio.run(starboard.bot._handle_frame(frame))


def highlight_row(message_id="m1"):
    return asyncio.run(starboard.bot.store.run(starboard.get_highlight, message_id))


def test_below_threshold_does_nothing():
    client = setup()
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD - 1))
    assert client.sent == []


def test_crossing_threshold_posts_a_highlight_crediting_the_author():
    client = setup()
    client.respond("GET", "/channels/c1/messages/m1", origin_message(content="look at this"))
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    assert len(client.sent) == 1
    sent = client.sent[0]
    assert sent["channel_id"] == "sb"
    assert sent["content"] == f"{starboard.STARBOARD_EMOJI} **{starboard.STARBOARD_THRESHOLD}**"
    assert sent["embeds"][0]["author"]["name"] == "Nick"
    assert sent["embeds"][0]["description"] == "look at this"
    assert highlight_row()["highlight_message_id"] == sent["id"]


def test_a_message_is_only_ever_highlighted_once():
    client = setup()
    client.respond("GET", "/channels/c1/messages/m1", origin_message())
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    highlight_id = client.sent[0]["id"]
    client.respond("PATCH", f"/channels/sb/messages/{highlight_id}", {"id": highlight_id, "content": "x"})
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD + 1))
    assert len(client.sent) == 1


def test_reaction_count_changes_edit_the_highlight_in_place():
    client = setup()
    client.respond("GET", "/channels/c1/messages/m1", origin_message())
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    highlight_id = client.sent[0]["id"]
    new_count = starboard.STARBOARD_THRESHOLD + 2
    client.respond("PATCH", f"/channels/sb/messages/{highlight_id}", {"id": highlight_id, "content": "x"})
    handle(reactions_frame(new_count))
    expected = ("PATCH", f"/channels/sb/messages/{highlight_id}", {"content": f"{starboard.STARBOARD_EMOJI} **{new_count}**"}, None)
    assert expected in client.calls
    assert len(client.sent) == 1  # edited in place, never reposted
    assert highlight_row()["count"] == new_count


def test_bot_authors_message_is_never_mirrored():
    client = setup()
    client.respond("GET", "/channels/c1/messages/m1", origin_message(author_id="bot-2", content="spam"))
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    assert client.sent == []
    assert highlight_row() is None


def test_a_gone_origin_message_skips_the_highlight_quietly():
    client = setup()
    client.respond("GET", "/channels/c1/messages/m1", ApiError(404, {"error": "not found"}))
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    assert client.sent == []


def test_restricted_origin_into_an_open_starboard_is_refused():
    """The one true leak: a private channel's content would show up to a starboard @everyone can already read."""
    client = setup(origin_restricted=True, starboard_restricted=False)
    client.respond("GET", "/channels/c1/messages/m1", origin_message(content="secret"))
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    assert client.sent == []
    assert highlight_row() is None


def test_open_origin_into_a_restricted_starboard_is_allowed():
    client = setup(origin_restricted=False, starboard_restricted=True)
    client.respond("GET", "/channels/c1/messages/m1", origin_message())
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    assert len(client.sent) == 1


def test_two_restricted_channels_are_allowed_a_documented_gap():
    """`Channel.restricted` is one @everyone-wide boolean, not a role-level audience diff - see README.md."""
    client = setup(origin_restricted=True, starboard_restricted=True)
    client.respond("GET", "/channels/c1/messages/m1", origin_message())
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    assert len(client.sent) == 1


def test_image_attachments_are_carried_over_but_not_other_files():
    client = setup()
    client.respond("GET", "/channels/c1/messages/m1", origin_message(attachments=[
        {"id": "att-1", "content_type": "image/png", "filename": "x.png", "size": 10},
        {"id": "att-2", "content_type": "text/plain", "filename": "x.txt", "size": 10},
    ]))
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    assert client.sent[0]["attachment_ids"] == ["att-1"]


def test_message_edited_reposts_with_the_updated_content():
    client = setup()
    client.respond("GET", "/channels/c1/messages/m1", origin_message(content="before"))
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    old_id = client.sent[0]["id"]
    client.respond("DELETE", f"/channels/sb/messages/{old_id}", None)
    handle({
        "type": "message.edited", "channel_id": "c1", "seq": 2,
        "message": origin_message(content="after"),
    })
    assert ("DELETE", f"/channels/sb/messages/{old_id}") in [(m, p) for m, p, _, _ in client.calls]
    assert len(client.sent) == 2
    assert client.sent[1]["embeds"][0]["description"] == "after"
    row = highlight_row()
    assert row["highlight_message_id"] == client.sent[1]["id"]
    assert row["count"] == starboard.STARBOARD_THRESHOLD


def test_message_deleted_removes_the_highlight():
    client = setup()
    client.respond("GET", "/channels/c1/messages/m1", origin_message())
    handle(reactions_frame(starboard.STARBOARD_THRESHOLD))
    highlight_id = client.sent[0]["id"]
    client.respond("DELETE", f"/channels/sb/messages/{highlight_id}", None)
    handle({"type": "message.deleted", "channel_id": "c1", "message_id": "m1"})
    assert ("DELETE", f"/channels/sb/messages/{highlight_id}") in [(m, p) for m, p, _, _ in client.calls]
    assert highlight_row() is None


def test_deleting_an_unstarred_message_is_a_quiet_no_op():
    setup()
    handle({"type": "message.deleted", "channel_id": "c1", "message_id": "never-starred"})
    assert highlight_row("never-starred") is None


def test_weekly_digest_lists_the_top_highlights():
    client = setup()
    conn = starboard.bot.store.connection
    conn.execute(
        "INSERT INTO highlights (origin_message_id, origin_channel_id, highlight_message_id, count, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("m1", "c1", "h1", 9, int(time.time())),
    )
    conn.commit()
    asyncio.run(starboard.post_weekly_digest())
    assert "9" in client.sent[-1]["content"]
    assert "general" in client.sent[-1]["content"]


def test_init_db_is_idempotent():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    starboard.init_db(conn)
    starboard.init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM highlights").fetchone()[0] == 0


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
