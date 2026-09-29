#!/usr/bin/env python3
"""The shared half: service, store, history dedupe, poll loop, command guard, library command, chooser; run directly: python3 test_bot.py."""

import asyncio
import io
import os
import sqlite3
import sys
import urllib.error
import uuid
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arrkit import history, library, poller, service, store  # noqa: E402
from arrkit.chooser import Chooser  # noqa: E402
from arrkit.guard import Guard  # noqa: E402
from arrkit.testkit import FakeApi, Harness, Patched, labels, newest_buttons  # noqa: E402
from slimbots import ApiError, Bot  # noqa: E402

SERVICE = service.Service("demo", "/api/v3")
GUARD = Guard(SERVICE)
CHOOSER = Chooser("demopick:", "demo add", library.label, "add")
API = FakeApi()


def item(rid, kind, ident="a", quality="HD", data=None, source="src"):
    return {"id": rid, "eventType": kind, "ident": ident, "quality": {"quality": {"name": quality}}, "data": data or {}, "sourceTitle": source}


def event_from(record):
    return {"ident": record["ident"], "quality": history.quality_of(record), "source": record["sourceTitle"], "id": record["id"],
            "message": record["data"].get("message", ""), "indexer": record["data"].get("indexer", "")}


def plan(conn, records):
    upgraded = history.upgrade_idents(records, "deleted", lambda r: r["ident"])
    events = history.plan_events(conn, records, event_from, upgraded)
    for event in events:
        store.mark_announced(conn, [(event["key"], event["detail"])])
    return [(e["post"], e["ident"]) for e in events]


def fresh_db():
    conn = sqlite3.connect(":memory:")
    store.init_db(conn)
    return conn


def test_a_first_import_is_downloaded_and_the_same_quality_again_is_silent():
    conn = fresh_db()
    assert plan(conn, [item(1, "downloadFolderImported")]) == [("downloaded", "a")]
    assert plan(conn, [item(2, "downloadFolderImported")]) == []


def test_a_better_quality_is_one_upgrade_and_then_silent():
    conn = fresh_db()
    plan(conn, [item(1, "downloadFolderImported")])
    assert plan(conn, [item(2, "downloadFolderImported", quality="4K")]) == [("upgraded", "a")]
    assert plan(conn, [item(3, "downloadFolderImported", quality="4K")]) == []


def test_two_imports_of_one_item_in_one_batch_announce_once():
    assert plan(fresh_db(), [item(1, "downloadFolderImported"), item(2, "downloadFolderImported")]) == [("downloaded", "a")]


def test_an_upgrade_delete_in_the_batch_makes_a_first_sighting_an_upgrade():
    batch = [item(1, "deleted", data={"reason": "Upgrade"}), item(2, "downloadFolderImported", quality="4K")]
    assert plan(fresh_db(), batch) == [("upgraded", "a")]


def test_a_manual_delete_is_not_an_upgrade():
    batch = [item(1, "deleted", data={"reason": "Manual"}), item(2, "downloadFolderImported")]
    assert plan(fresh_db(), batch) == [("downloaded", "a")]


def test_a_grab_inside_the_window_is_dropped_and_one_after_it_is_kept():
    conn = fresh_db()
    assert plan(conn, [item(1, "grabbed")]) == [("grabbed", "a")]
    assert plan(conn, [item(2, "grabbed")]) == []
    conn.execute("UPDATE announced SET at = at - ?", (history.GRAB_WINDOW_SECONDS + 5,))
    assert plan(conn, [item(3, "grabbed")]) == [("grabbed", "a")]


def test_a_failure_is_announced_once_per_release_not_once_per_item():
    conn = fresh_db()
    assert plan(conn, [item(1, "downloadFailed", source="r1")]) == [("failed", "a")]
    assert plan(conn, [item(2, "downloadFailed", source="r1")]) == []
    assert plan(conn, [item(3, "downloadFailed", source="r2")]) == [("failed", "a")]


def test_unrelated_event_types_and_unmappable_records_are_skipped():
    conn = fresh_db()
    assert plan(conn, [item(1, "seriesAdded")]) == []
    assert history.plan_events(conn, [item(2, "grabbed")], lambda r: None, set()) == []


def test_the_message_id_is_the_same_on_every_retry_and_differs_by_kind():
    group = [{"key": "k1", "detail": "HD", "id": 1}]
    ns = uuid.UUID(int=1)
    first = history.make_post(ns, "grabbed", "x", group)
    assert first["message_id"] == history.make_post(ns, "grabbed", "x", group)["message_id"]
    assert first["message_id"] != history.make_post(ns, "failed", "x", group)["message_id"]


def test_the_cursor_only_moves_forward():
    conn = fresh_db()
    store.advance_cursor(conn, 50)
    store.advance_cursor(conn, 9)
    assert store.get_cursor(conn) == 50


def test_fetch_since_pages_back_only_while_a_page_is_all_new_and_returns_oldest_first():
    records = [{"id": i} for i in range(1, 251)]

    def api(method, path, params=None, body=None):
        ordered = sorted(records, key=lambda r: -r["id"])
        size, page = params["pageSize"], params["page"]
        return {"records": ordered[(page - 1) * size:page * size]}

    got = history.fetch_since(api, 120, {})
    assert [r["id"] for r in got][:2] == [121, 122] and len(got) == 130


def test_the_url_rule_allows_loopback_and_bare_service_names_and_refuses_public_plaintext():
    for url, ok in (("http://sonarr:8989", True), ("http://127.0.0.1:1", True), ("http://x.example.com", False), ("https://x.example.com", True)):
        svc = service.Service("demo", "/api")
        svc.url = url
        assert (svc.problem() is None) is ok, url


def test_the_api_key_goes_in_a_header_never_the_url_and_a_401_is_an_auth_error():
    svc = service.Service("demo", "/api/v3")
    svc.url, svc.key = "http://demo:1", "sekrit"
    seen = {}

    def fake_open(request, timeout):
        seen["url"], seen["key"] = request.full_url, request.get_header("X-api-key")
        return io.BytesIO(b'{"ok": true}')

    with mock.patch("urllib.request.urlopen", fake_open):
        assert svc.call("GET", "/x", {"a": 1}) == {"ok": True}
    assert seen["key"] == "sekrit" and "sekrit" not in seen["url"] and seen["url"] == "http://demo:1/api/v3/x?a=1"

    def rejected(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)

    with mock.patch("urllib.request.urlopen", rejected):
        try:
            svc.call("GET", "/x")
        except service.AuthError as err:
            assert "sekrit" not in str(err)
            return
    raise AssertionError("expected AuthError")


bot = Bot(prefix="!", require_channels=True)
CORE = SimpleNamespace(SERVICE=SERVICE, api=API)
CORE.lookup = lambda term: CORE.api("GET", "/lookup", {"term": term})
CORE.add = lambda i: CORE.api("POST", "/add", body=i)
SPEC = library.Spec(
    name="demo", noun="thing", queue_params={}, queue_line=lambda r: f"- {r['title']} - {r['status']}", calendar_params={},
    calendar_line=lambda i, _s, _e: f"{i['when']} {i['title']}", calendar_what="things due", added_text="working on it",
)
LIBRARY = library.Library(CORE, SPEC, GUARD, CHOOSER)
LIBRARY.setup(bot)
harness = Harness(bot, store.init_db)


def fresh(**routes):
    client = harness.setup()
    GUARD.cooldown._last.clear()
    CHOOSER._picks.clear()
    api = FakeApi(**routes)
    CORE.api = api
    return client, api


def say(text, msg_id="m1", author="u1"):
    harness.process(harness.message(text, msg_id, author))


def test_help_uses_the_bots_own_prefix():
    client, _ = fresh()
    saved, bot.prefix = bot.prefix, "?"
    try:
        harness.process({"id": "m9", "author_id": "u1", "channel_id": "c1", "content": "?demo help"})
    finally:
        bot.prefix = saved
    assert "`?demo search <thing>`" in client.sent[-1]["content"] and "`!" not in client.sent[-1]["content"]


def test_search_marks_what_is_in_the_library_and_says_when_nothing_matches():
    client, _ = fresh(**{"/lookup": [{"title": "A", "year": 2000, "id": 1}, {"title": "B", "year": 2001}]})
    say("!demo search a")
    lines = client.sent[-1]["content"].splitlines()
    assert "- A (2000) - in the library" in lines and "- B (2001)" in lines
    GUARD.cooldown._last.clear()
    CORE.api.routes["/lookup"] = []
    say("!demo search zzz", "m2")
    assert 'nothing found for "zzz"' in client.sent[-1]["content"]


def test_bad_input_is_free_and_never_reaches_the_service():
    client, api = fresh()
    say("!demo search")
    say("!demo search " + "x" * 200, "m2")
    say("!demo calendar 99", "m3")
    assert api.calls == [] and all("between" in m["content"] or "at most" in m["content"] for m in client.sent)
    assert GUARD.cooldown._last == {}


def test_commands_are_cooldown_limited_per_member():
    client, _ = fresh(**{"/lookup": [{"title": "A"}]})
    say("!demo search a")
    say("!demo search a", "m2")
    assert "try again" in client.sent[-1]["content"]
    say("!demo search a", "m3", author="u2")
    assert "try again" not in client.sent[-1]["content"]


def test_an_unreachable_service_and_a_rejected_key_each_get_a_sentence():
    client, api = fresh()
    api.fail_with = OSError("refused")
    say("!demo queue")
    assert "demo is unavailable right now" in client.sent[-1]["content"]
    GUARD.cooldown._last.clear()
    api.fail_with = service.AuthError("no")
    say("!demo queue", "m2")
    assert "rejected the bot's api key" in client.sent[-1]["content"]


def test_queue_and_calendar_render_the_bots_lines_and_count_the_overflow():
    client, api = fresh(**{"/queue": {"records": [{"title": "x", "status": "downloading"}], "totalRecords": 12}, "/calendar": [{"when": "2026-10-02", "title": "B"}, {"when": "2026-10-01", "title": "A"}]})
    say("!demo queue")
    assert "12 in the queue:\n- x - downloading\n...and 11 more." in client.sent[-1]["content"]
    GUARD.cooldown._last.clear()
    say("!demo calendar 3", "m2")
    reply = client.sent[-1]["content"]
    assert reply.index("2026-10-01 A") < reply.index("2026-10-02 B")
    api.routes["/queue"] = {"records": [], "totalRecords": 0}
    GUARD.cooldown._last.clear()
    say("!demo queue", "m3")
    assert "empty" in client.sent[-1]["content"]


LOOKUP = [{"title": "A", "year": 2000, "tid": 1}, {"title": "Old", "year": 1990, "id": 7}, {"title": "C", "year": 2020, "tid": 3}]


def test_the_chooser_offers_only_what_is_not_in_the_library_and_choosing_adds_once():
    client, api = fresh(**{"/lookup": LOOKUP})
    say("!demo add a")
    assert labels(newest_buttons(client)) == ["A (2000)", "C (2020)", "Cancel"]
    harness.press_chooser(client, ("demopick:sel:1", "u1"), ("demopick:sel:1", "u1"))
    assert len(api.posts()) == 1 and api.posts()[0][3]["tid"] == 3
    assert "added **C (2020)** - working on it." in client.edited[-1]["content"]
    assert client.component_edits[-1]["components"] == [] and "expired" in client.ephemerals[-1]["content"]


def test_only_the_invoker_may_press_and_cancel_closes_without_adding():
    client, api = fresh(**{"/lookup": LOOKUP})
    say("!demo add a")
    harness.press_chooser(client, ("demopick:sel:0", "u2"))
    assert api.posts() == [] and "only Nick can choose" in client.ephemerals[-1]["content"]
    harness.press_chooser(client, ("demopick:cancel", "u1"))
    assert client.edited[-1]["content"] == "cancelled." and api.posts() == []


def test_a_malformed_or_out_of_range_button_id_adds_nothing():
    client, api = fresh(**{"/lookup": LOOKUP})
    say("!demo add a")
    harness.press_chooser(client, ("demopick:sel:abc", "u1"), ("demopick:sel:9", "u1"), ("demopick:nuke", "u1"))
    assert api.posts() == []


def test_an_expired_chooser_refuses_and_closes():
    client, api = fresh(**{"/lookup": LOOKUP})
    say("!demo add a")
    next(iter(CHOOSER._picks.values())).created_at -= 301
    harness.press_chooser(client, ("demopick:sel:0", "u1"))
    assert api.posts() == [] and "timed out - `!demo add` again" in client.edited[-1]["content"]


def test_the_service_refusing_the_add_is_reported_in_the_chooser():
    client, api = fresh(**{"/lookup": LOOKUP})
    api.refuse_posts = RuntimeError("no root folder")
    say("!demo add a")
    harness.press_chooser(client, ("demopick:sel:0", "u1"))
    assert "could not add **A (2000)**: no root folder." in client.edited[-1]["content"]


def test_everything_already_in_the_library_opens_no_chooser():
    client, _ = fresh(**{"/lookup": [LOOKUP[1]]})
    say("!demo add old")
    assert "already in the library" in client.sent[-1]["content"] and not client.sent[-1].get("components")


def history_core(records):
    """A minimal core for the poll loop: history served from a list, one post per record."""
    def fetch(cursor):
        return sorted((r for r in records if r["id"] > cursor), key=lambda r: r["id"])

    def bootstrap(conn):
        store.advance_cursor(conn, max((r["id"] for r in records), default=0))

    def plan_events(conn, batch):
        return [
            {"post": "downloaded", "key": f"i|{r['id']}", "detail": "HD", "id": r["id"]}
            for r in batch if store.get_announced(conn, f"i|{r['id']}") is None
        ]

    def build_posts(events):
        ns = uuid.UUID(int=2)
        return [history.make_post(ns, "downloaded", f"item {e['id']}", [e], quality="HD", indexer="", message="") for e in events]

    return SimpleNamespace(fetch_history_since=fetch, bootstrap_cursor=bootstrap, plan_events=plan_events, build_posts=build_posts)


def test_the_first_poll_starts_at_the_newest_record_and_a_later_one_posts_it_once():
    client = harness.setup()
    records = [{"id": 7}]
    core = history_core(records)
    asyncio.run(poller.history_poll_once(bot, core))
    assert client.sent == [] and asyncio.run(bot.store.run(store.get_cursor)) == 7
    records.append({"id": 8})
    asyncio.run(poller.history_poll_once(bot, core))
    asyncio.run(poller.history_poll_once(bot, core))
    assert len(client.sent) == 1 and client.sent[0]["channel_id"] == "c1"
    assert client.sent[0]["embeds"][0]["title"] == "Downloaded: item 8 [HD]"


def test_a_failed_send_keeps_the_cursor_and_retries_without_repeating_what_landed():
    client = harness.setup()
    records = [{"id": 1}]
    core = history_core(records)
    asyncio.run(poller.history_poll_once(bot, core))
    records.extend([{"id": 2}, {"id": 3}])
    real, attempts = client.send, []

    async def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 2:
            raise ApiError(500, "boom")
        return await real(*args, **kwargs)

    client.send = flaky
    asyncio.run(poller.history_poll_once(bot, core))
    assert asyncio.run(bot.store.run(store.get_cursor)) == 1
    client.send = real
    asyncio.run(poller.history_poll_once(bot, core))
    assert [m["embeds"][0]["title"] for m in client.sent] == ["Downloaded: item 2 [HD]", "Downloaded: item 3 [HD]"]
    assert asyncio.run(bot.store.run(store.get_cursor)) == 3


def test_the_poll_loop_stops_on_a_rejected_key_and_survives_an_outage():
    async def rejected():
        raise service.AuthError("no")

    try:
        asyncio.run(poller.run_forever(rejected, SimpleNamespace(name="demo", poll_seconds=0)))
    except service.AuthError:
        pass
    else:
        raise AssertionError("a rejected key must end the loop")
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("down")
        raise service.AuthError("stop")

    try:
        asyncio.run(poller.run_forever(flaky, SimpleNamespace(name="demo", poll_seconds=0)))
    except service.AuthError:
        pass
    assert len(calls) == 2


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
