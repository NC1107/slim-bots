#!/usr/bin/env python3
"""Request-state dedupe, posting, and command tests with the Seerr API faked; run directly: python3 test_bot.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SEERR_URL", "http://jellyseerr:5055")
os.environ.setdefault("SEERR_API_KEY", "fake-key")

import bot as seerr  # noqa: E402
import seerr_cog  # noqa: E402
import seerr_core as core  # noqa: E402
from arrkit.service import AuthError  # noqa: E402
from arrkit.testkit import FakeApi, Harness, Patched  # noqa: E402

harness = Harness(seerr.bot, core.init_db)


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


def seerr_api(**more):
    routes = {
        "/request": lambda params: {"results": api.requests} if params.get("filter") != "pending" else api.pending,
        "/search": lambda _p: {"results": api.search_results}, "/user": lambda _p: {"results": api.users},
    }
    api = FakeApi(**routes, **more)
    api.requests, api.search_results, api.users = [], [], []
    api.pending = {"results": [], "pageInfo": {"results": 0}}
    api.fallback = lambda path, _params: {"title": f"Title {path.rsplit('/', 1)[-1]}", "releaseDate": "2021-05-01"}
    return api


def setup():
    seerr_cog.GUARD.cooldown._last.clear()
    seerr_cog.CHOOSER._picks.clear()
    core.SEERR_DEFAULT_USER_ID = None
    return harness.setup()


def message(text, msg_id="m1", author="u1"):
    return harness.message(text, msg_id, author)


def process(*messages):
    harness.process(*messages)


def poll(api):
    with Patched(core, "api", api):
        asyncio.run(seerr.poll_once())


def test_the_first_poll_announces_nothing_and_a_later_request_posts_with_buttons():
    client = setup()
    fake = seerr_api()
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
    fake = seerr_api()
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
    fake = seerr_api()
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
    fake = seerr_api()
    with Patched(core, "api", fake):
        process(message("!requests"))
        assert "no requests are waiting" in client.sent[-1]["content"]
        seerr_cog.GUARD.cooldown._last.clear()
        fake.pending = {"results": [request(1, tmdb=7)], "pageInfo": {"results": 12}}
        process(message("!requests", "m2"))
    reply = client.sent[-1]["content"]
    assert "12 waiting for approval" in reply and "Title 7 (2021) [movie] - amy" in reply
    assert "...and 11 more." in reply


def test_request_without_a_link_or_default_user_asks_to_link_first():
    client = setup()
    fake = seerr_api()
    with Patched(core, "api", fake):
        process(message("!request dune"))
    assert "link your seerr account first" in client.sent[-1]["content"] and not any(c[1] == "/search" for c in fake.calls)


def test_link_finds_the_user_by_any_of_their_names_and_refuses_admins_and_taken_accounts():
    client = setup()
    fake = seerr_api()
    fake.users = [
        {"id": 5, "displayName": "amy", "jellyfinUsername": "Amy", "permissions": 32},
        {"id": 1, "displayName": "npc", "permissions": 2},
    ]
    with Patched(core, "api", fake):
        process(message("!request link AMY"))
        assert asyncio.run(seerr.bot.store.run(core.get_link, "u1"))[0] == 5
        seerr_cog.GUARD.cooldown._last.clear()
        process(message("!request link npc", "m2"))
        assert asyncio.run(seerr.bot.store.run(core.get_link, "u1"))[0] == 5
        seerr_cog.GUARD.cooldown._last.clear()
        process(message("!request link amy", "m3", author="u2"))
    texts = [e["content"] for e in client.ephemerals]
    assert any("linked you" in t for t in texts) and any("admin accounts cannot be linked" in t for t in texts) and any("already linked" in t for t in texts)
    assert asyncio.run(seerr.bot.store.run(core.get_link, "u2")) is None


def test_link_to_an_unknown_user_and_unlink_and_account_answer_plainly():
    client = setup()
    fake = seerr_api()
    with Patched(core, "api", fake):
        process(message("!request link ghost"))
        seerr_cog.GUARD.cooldown._last.clear()
        process(message("!request account", "m2"), message("!request unlink", "m3"))
    texts = [e["content"] for e in client.ephemerals]
    assert any('no seerr user called "ghost"' in t for t in texts)
    assert any("not linked" in t for t in texts) and any("not linked to a seerr user" in t for t in texts)


def test_an_unreachable_seerr_answers_with_a_sentence_not_a_traceback():
    client = setup()
    fake = seerr_api()
    fake.fail_with = OSError("refused")
    with Patched(core, "api", fake):
        process(message("!requests"))
    assert "unavailable right now" in client.sent[-1]["content"]


def test_a_rejected_api_key_says_so():
    client = setup()
    fake = seerr_api()
    fake.fail_with = AuthError("no")
    with Patched(core, "api", fake):
        process(message("!requests"))
    assert "api key" in client.sent[-1]["content"]


def test_an_oversized_title_is_refused_before_touching_seerr():
    client = setup()
    fake = seerr_api()
    with Patched(core, "api", fake):
        process(message("!request " + "x" * 200))
    assert "between" in client.sent[-1]["content"] and fake.calls == []


def test_the_approver_permission_must_be_a_real_slim_m_permission_name():
    from slimbots import Permissions
    saved = core.SEERR_APPROVER_PERMISSION
    try:
        core.SEERR_APPROVER_PERMISSION = "MANAGE_SERVER"
        assert core.permission_problem(Permissions) is None
        core.SEERR_APPROVER_PERMISSION = "MANAGE_NOTHING"
        assert "MANAGE_NOTHING" in core.permission_problem(Permissions)
    finally:
        core.SEERR_APPROVER_PERMISSION = saved


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
