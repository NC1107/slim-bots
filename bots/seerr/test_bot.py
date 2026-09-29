#!/usr/bin/env python3
"""Request-state dedupe, posting, and command tests with the Seerr API faked; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SEERR_URL", "http://jellyseerr:5055")
os.environ.setdefault("SEERR_API_KEY", "fake-key")

import bot as seerr  # noqa: E402
import seerr_core as core  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [
    {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []},
    {"id": "u2", "username": "amy", "display_name": "Amy", "is_bot": False, "is_webhook": False, "role_ids": []},
]


def request(rid, status=1, media_status=3, kind="movie", tmdb=100, by="amy"):
    return {"id": rid, "status": status, "requestedBy": {"id": 9, "displayName": by}, "media": {"mediaType": kind, "tmdbId": tmdb, "status": media_status}}


def details(media_type, tmdb_id):
    return {"title": f"Title {tmdb_id}", "year": "2021"}


def fresh_db():
    conn = sqlite3.connect(":memory:")
    core.init_db(conn)
    return conn


def announce(conn, requests):
    posts = core.plan_posts(conn, requests, details)
    for post in posts:
        core.mark_announced(conn, post["keys"])
    return posts


def lines(posts):
    return [core.render_text(p) for p in posts]


def test_a_new_pending_request_is_posted_once_with_decision_buttons():
    conn = fresh_db()
    posts = announce(conn, [request(1)])
    assert lines(posts) == ["Requested: **Title 100 (2021)** [movie] - requested by amy"]
    assert posts[0]["buttons"] is True
    assert announce(conn, [request(1)]) == []


def test_a_request_first_seen_already_approved_is_one_post_without_buttons():
    posts = announce(fresh_db(), [request(1, status=2)])
    assert lines(posts) == ["Approved: **Title 100 (2021)** [movie] - requested by amy"]
    assert posts[0]["buttons"] is False


def test_approval_after_the_request_post_is_a_second_post_and_then_silent():
    conn = fresh_db()
    announce(conn, [request(1)])
    assert lines(announce(conn, [request(1, status=2)])) == ["Approved: **Title 100 (2021)** [movie] - requested by amy"]
    assert announce(conn, [request(1, status=2)]) == []


def test_a_decline_is_announced_once():
    conn = fresh_db()
    announce(conn, [request(1)])
    assert lines(announce(conn, [request(1, status=3)]))[0].startswith("Declined:")
    assert announce(conn, [request(1, status=3)]) == []


def test_availability_is_announced_once_per_media_even_with_two_requesters():
    conn = fresh_db()
    announce(conn, [request(1, status=2), request(2, status=2, by="bob")])
    posts = announce(conn, [request(1, status=2, media_status=5), request(2, status=2, media_status=5, by="bob")])
    assert lines(posts) == ["Available: **Title 100 (2021)** [movie] - requested by amy, bob"]
    assert announce(conn, [request(1, status=2, media_status=5)]) == []


def test_partial_then_full_availability_are_each_announced_once():
    conn = fresh_db()
    announce(conn, [request(1, status=2, kind="tv", media_status=3)])
    assert lines(announce(conn, [request(1, status=2, kind="tv", media_status=4)]))[0].startswith("Partly available:")
    assert lines(announce(conn, [request(1, status=2, kind="tv", media_status=5)]))[0].startswith("Available:")


def test_a_show_reads_as_a_show():
    assert "[show]" in lines(announce(fresh_db(), [request(1, kind="tv")]))[0]


def test_the_cold_start_marks_the_backlog_and_announces_nothing():
    conn = fresh_db()
    backlog = [request(1, status=2, media_status=5), request(2)]
    assert core.bootstrap(conn, backlog) is True
    assert announce(conn, backlog) == []
    assert core.bootstrap(conn, backlog) is False
    assert lines(announce(conn, [request(3)])) != []


def test_links_are_one_to_one_and_removable():
    conn = fresh_db()
    core.set_link(conn, "u1", 5, "amy")
    assert core.get_link(conn, "u1") == (5, "amy") and core.owner_of(conn, 5) == "u1"
    assert core.remove_link(conn, "u1") is True and core.get_link(conn, "u1") is None


class FakeSeerr:
    """Routes `core.api` calls by path; records every call so a test can assert what was sent."""

    def __init__(self):
        self.calls = []
        self.requests = []
        self.search_results = []
        self.users = []
        self.fail_with = None
        self.refuse_posts = None
        self.pending = {"results": [], "pageInfo": {"results": 0}}

    def __call__(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if self.fail_with:
            raise self.fail_with
        if method == "POST":
            if self.refuse_posts:
                raise self.refuse_posts
            return {"id": 500}
        if path == "/request":
            if (params or {}).get("filter") == "pending":
                return self.pending
            return {"results": self.requests}
        if path == "/search":
            return {"results": self.search_results}
        if path == "/user":
            return {"results": self.users}
        return {"title": f"Title {path.rsplit('/', 1)[-1]}", "releaseDate": "2021-05-01"}


class Patched:
    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        self.saved = core.api
        core.api = self.fake
        return self.fake

    def __exit__(self, *_exc):
        core.api = self.saved


def setup():
    seerr.bot.store = Store(":memory:", migrate=core.init_db)
    asyncio.run(seerr.bot.store.open())
    seerr.bot.channels = {"c1"}
    core._command_cooldown._last.clear()
    core.SEERR_DEFAULT_USER_ID = None
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    client.edited = []

    async def record_edit(channel_id, message_id, content):
        client.edited.append({"channel_id": channel_id, "message_id": message_id, "content": content})

    client.edit_message = record_edit
    seerr.bot.client = client
    seerr.bot.space = Space(client)
    seerr.bot.authors = AuthorFilter(client, space=seerr.bot.space, ignore_bots=True)
    seerr.bot.me_id = "bot-1"
    asyncio.run(seerr.bot.space.refresh_members())
    return client


def message(content, msg_id="m1", author="u1"):
    return {"id": msg_id, "author_id": author, "channel_id": "c1", "content": content}


def process(*messages):
    async def run():
        for msg in messages:
            await seerr.bot.process_message(msg)

    asyncio.run(run())


def poll(fake):
    with Patched(fake):
        asyncio.run(seerr.poll_once())


def test_the_first_poll_announces_nothing_and_a_later_request_posts_with_buttons():
    client = setup()
    fake = FakeSeerr()
    fake.requests = [request(1, status=2, media_status=5)]
    poll(fake)
    assert client.sent == []
    fake.requests.append(request(2))
    poll(fake)
    poll(fake)
    assert len(client.sent) == 1
    assert client.sent[0]["channel_id"] == "c1" and client.sent[0]["content"].startswith("Requested:")
    labels = [b["label"] for row in client.sent[0]["components"] for b in row["buttons"]]
    assert labels == ["Approve", "Decline"]


def test_a_failed_send_retries_without_repeating_what_landed():
    client = setup()
    fake = FakeSeerr()
    fake.requests = [request(1, status=2, media_status=5)]
    poll(fake)
    fake.requests += [request(2, tmdb=2), request(3, tmdb=3)]
    real_send, attempts = client.send, []

    async def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 2:
            raise seerr.ApiError(500, "boom")
        return await real_send(*args, **kwargs)

    client.send = flaky
    poll(fake)
    client.send = real_send
    poll(fake)
    contents = [m["content"] for m in client.sent]
    assert len(contents) == 2 and len(set(contents)) == 2


def test_a_seerr_outage_during_a_poll_raises_for_the_loop_to_retry():
    setup()
    fake = FakeSeerr()
    fake.requests = [request(1)]
    poll(fake)
    fake.fail_with = OSError("down")
    try:
        poll(fake)
    except OSError:
        return
    raise AssertionError("expected the outage to propagate")


def test_requests_lists_pending_and_says_so_when_there_are_none():
    client = setup()
    fake = FakeSeerr()
    with Patched(fake):
        process(message("!requests"))
        assert "no requests are waiting" in client.sent[-1]["content"]
        core._command_cooldown._last.clear()
        fake.pending = {"results": [request(1, tmdb=7)], "pageInfo": {"results": 12}}
        process(message("!requests", "m2"))
    reply = client.sent[-1]["content"]
    assert "12 waiting for approval" in reply and "Title 7 (2021) [movie] - amy" in reply
    assert "...and 11 more." in reply


def test_request_without_a_link_or_default_user_asks_to_link_first():
    client = setup()
    fake = FakeSeerr()
    with Patched(fake):
        process(message("!request dune"))
    assert "link your seerr account first" in client.sent[-1]["content"] and not any(c[1] == "/search" for c in fake.calls)


def test_link_finds_the_user_by_any_of_their_names_and_refuses_admins_and_taken_accounts():
    client = setup()
    fake = FakeSeerr()
    fake.users = [
        {"id": 5, "displayName": "amy", "jellyfinUsername": "Amy", "permissions": 32},
        {"id": 1, "displayName": "npc", "permissions": 2},
    ]
    with Patched(fake):
        process(message("!request link AMY"))
        assert asyncio.run(seerr.bot.store.run(core.get_link, "u1"))[0] == 5
        core._command_cooldown._last.clear()
        process(message("!request link npc", "m2"))
        assert asyncio.run(seerr.bot.store.run(core.get_link, "u1"))[0] == 5
        core._command_cooldown._last.clear()
        process(message("!request link amy", "m3", author="u2"))
    texts = [e["content"] for e in client.ephemerals]
    assert any("linked you" in t for t in texts) and any("admin accounts cannot be linked" in t for t in texts) and any("already linked" in t for t in texts)
    assert asyncio.run(seerr.bot.store.run(core.get_link, "u2")) is None


def test_link_to_an_unknown_user_and_unlink_and_account_answer_plainly():
    client = setup()
    fake = FakeSeerr()
    with Patched(fake):
        process(message("!request link ghost"))
        core._command_cooldown._last.clear()
        process(message("!request account", "m2"), message("!request unlink", "m3"))
    texts = [e["content"] for e in client.ephemerals]
    assert any('no seerr user called "ghost"' in t for t in texts)
    assert any("not linked" in t for t in texts) and any("not linked to a seerr user" in t for t in texts)


def test_an_unreachable_seerr_answers_with_a_sentence_not_a_traceback():
    client = setup()
    fake = FakeSeerr()
    fake.fail_with = OSError("refused")
    with Patched(fake):
        process(message("!requests"))
    assert "unavailable right now" in client.sent[-1]["content"]


def test_a_rejected_api_key_says_so():
    client = setup()
    fake = FakeSeerr()
    fake.fail_with = core.SeerrAuthError("no")
    with Patched(fake):
        process(message("!requests"))
    assert "api key" in client.sent[-1]["content"]


def test_an_oversized_title_is_refused_before_touching_seerr():
    client = setup()
    fake = FakeSeerr()
    with Patched(fake):
        process(message("!request " + "x" * 200))
    assert "between" in client.sent[-1]["content"] and fake.calls == []


def test_the_config_check_rejects_a_public_plaintext_url_and_a_bogus_permission_name():
    from slimbots import Permissions
    saved = core.SEERR_URL, core.SEERR_APPROVER_PERMISSION
    try:
        for url, ok in (("http://jellyseerr:5055", True), ("http://127.0.0.1:5055", True), ("http://seerr.example.com", False), ("https://seerr.example.com", True)):
            core.SEERR_URL = url
            assert (core.check_seerr_config(Permissions) is None) is ok, url
        core.SEERR_URL, core.SEERR_APPROVER_PERMISSION = "https://x.example.com", "MANAGE_NOTHING"
        assert "MANAGE_NOTHING" in core.check_seerr_config(Permissions)
    finally:
        core.SEERR_URL, core.SEERR_APPROVER_PERMISSION = saved


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
