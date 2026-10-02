#!/usr/bin/env python3
"""The poll cycle with a fake GitHub transport and a fake slimm client; run directly: python3 test_bot.py."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as releases  # noqa: E402
import feed as feedmod  # noqa: E402
import fixtures  # noqa: E402
from slimbots import Store  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

BASE = "https://github.com/Slim-m-org/slim-m/releases/tag/"


def rel(rid, tag, body="", draft=False, prerelease=False, published="2026-10-02T00:00:00Z"):
    return {"id": rid, "tag_name": tag, "body": body, "draft": draft, "prerelease": prerelease, "published_at": published, "html_url": BASE + tag}


R_CLIENT = rel(3, "client-v0.93.0", fixtures.CLIENT_0_93_0, published="2026-10-02T03:00:00Z")
R_SERVER = rel(2, "server-v0.81.0", fixtures.SERVER_0_81_0, published="2026-10-02T02:00:00Z")
R_OLD = rel(1, "client-v0.92.0", fixtures.CLIENT_0_92_0, published="2026-10-01T00:00:00Z")


class Rig:
    """One bot wired to an in-memory store, a recording client and a scripted GitHub answer."""

    def __init__(self, store=None, announce_latest=False):
        self.client = FakeAsyncClient(me_id="bot-1")
        self.store = store or Store(":memory:", migrate=releases.init_db)
        asyncio.run(self.store.open())
        releases.bot.store, releases.bot.client, releases.bot.channels = self.store, self.client, {"c1"}
        releases.config.announce_latest = announce_latest
        self.answer = (200, {"etag": 'W/"1"'}, b"[]")
        self.seen_headers = []
        self.feed = feedmod.Feed("Slim-m-org/slim-m", transport=self.transport)

    def transport(self, url, headers):
        self.seen_headers.append(headers)
        if "if-none-match" in headers and headers["if-none-match"] == self.answer[1].get("etag"):
            return 304, {}, b""
        return self.answer

    def serve(self, *items, etag='W/"1"'):
        self.answer = (200, {"etag": etag}, json.dumps(list(items)).encode())

    def poll(self):
        return asyncio.run(releases.poll_once(self.feed))

    def restart(self, **kwargs):
        """A new process: same database, fresh feed (no remembered etag)."""
        return Rig(store=self.store, **kwargs)


def test_the_first_run_posts_nothing_and_records_what_exists():
    rig = Rig()
    rig.serve(R_CLIENT, R_SERVER, R_OLD)
    rig.poll()
    assert rig.client.sent == []
    rig.serve(R_CLIENT, R_SERVER, R_OLD, etag='W/"2"')
    rig.poll()
    assert rig.client.sent == []


def test_a_new_release_posts_once_with_title_link_and_notes():
    rig = Rig()
    rig.serve(R_OLD)
    rig.poll()
    rig.serve(R_CLIENT, R_OLD, etag='W/"2"')
    rig.poll()
    rig.poll()
    assert len(rig.client.sent) == 1
    sent = rig.client.sent[0]
    assert sent["channel_id"] == "c1"
    assert sent["content"].startswith("**client 0.93.0**\n" + BASE + "client-v0.93.0\n\n- hold to lift")
    assert not sent.get("components"), "the url is in the text, so no second link"


def test_a_restart_does_not_repost_and_a_release_missed_while_down_posts_oldest_first():
    rig = Rig()
    rig.serve(R_OLD)
    rig.poll()
    rig.serve(R_CLIENT, R_OLD, etag='W/"2"')
    rig.poll()
    again = rig.restart()
    again.serve(R_CLIENT, R_OLD)
    again.poll()
    assert again.client.sent == []
    again.serve(R_CLIENT, R_SERVER, R_OLD, etag='W/"3"')
    again.poll()
    assert [m["content"].split("\n")[0] for m in again.client.sent] == ["**server 0.81.0**"]
    two = rig.restart()
    two.serve(rel(5, "server-v0.82.0", published="2026-10-03T00:00:00Z"), rel(4, "client-v0.94.0", published="2026-10-02T09:00:00Z"), R_CLIENT, R_SERVER, R_OLD)
    two.poll()
    assert [m["content"].split("\n")[0] for m in two.client.sent] == ["**client 0.94.0**", "**server 0.82.0**"]


def test_only_configured_prefixes_post_and_schema_releases_stay_quiet():
    rig = Rig()
    rig.serve(R_OLD)
    rig.poll()
    rig.serve(rel(9, "schema-v0.81.0", fixtures.SCHEMA_0_81_0), rel(8, "weird", "x"), R_OLD, etag='W/"2"')
    rig.poll()
    assert rig.client.sent == []
    releases.config.prefixes = ("schema-v",)
    try:
        rig.serve(rel(9, "schema-v0.81.0", fixtures.SCHEMA_0_81_0), R_OLD, etag='W/"3"')
        rig.poll()
        assert rig.client.sent == []
    finally:
        releases.config.prefixes = ("client-v", "server-v")


def test_drafts_and_prereleases_are_skipped_and_post_if_they_later_publish():
    rig = Rig()
    rig.serve(R_OLD)
    rig.poll()
    draft = rel(7, "client-v0.95.0", "* a thing", draft=True)
    pre = rel(6, "server-v0.83.0-rc.1", "* a thing", prerelease=True)
    rig.serve(draft, pre, R_OLD, etag='W/"2"')
    rig.poll()
    assert rig.client.sent == []
    rig.serve(dict(draft, draft=False), dict(pre, prerelease=False), R_OLD, etag='W/"3"')
    rig.poll()
    assert len(rig.client.sent) == 2


def test_a_release_with_no_notes_posts_just_title_and_link():
    rig = Rig()
    rig.serve(R_OLD)
    rig.poll()
    rig.serve(rel(4, "server-v0.90.0", None), R_OLD, etag='W/"2"')
    rig.poll()
    assert rig.client.sent[0]["content"] == f"**server 0.90.0**\n{BASE}server-v0.90.0"


def test_announce_latest_posts_only_the_newest_matching_release_on_the_first_run():
    rig = Rig(announce_latest=True)
    rig.serve(R_OLD, R_SERVER, R_CLIENT)
    rig.poll()
    assert [m["content"].split("\n")[0] for m in rig.client.sent] == ["**client 0.93.0**"]
    rig.poll()
    assert len(rig.client.sent) == 1


def test_an_unchanged_list_costs_a_304_and_posts_nothing():
    rig = Rig()
    rig.serve(R_OLD)
    rig.poll()
    rig.answer = (304, {}, b"")
    rig.poll()
    assert rig.seen_headers[-1]["if-none-match"] == 'W/"1"'
    assert rig.client.sent == []


def test_a_failed_send_retries_next_cycle_and_never_posts_twice():
    rig = Rig()
    rig.serve(R_OLD)
    rig.poll()
    rig.serve(R_CLIENT, R_OLD, etag='W/"2"')
    real_call = rig.client.call

    async def failing(*args, **kwargs):
        raise ApiError(503, "down")

    rig.client.call = failing
    assert rig.poll() is None
    rig.client.call = real_call
    rig.poll()
    rig.poll()
    assert len(rig.client.sent) == 1
    assert rig.seen_headers[-1].get("if-none-match") == 'W/"2"'


def test_a_revoked_token_is_fatal_rather_than_retried_forever():
    rig = Rig()
    rig.serve(R_OLD)
    rig.poll()
    rig.serve(R_CLIENT, R_OLD, etag='W/"2"')

    async def revoked(*args, **kwargs):
        raise ApiError(401, "nope")

    rig.client.call = revoked
    try:
        rig.poll()
    except ApiError:
        return
    raise AssertionError("a 401 should propagate")


def test_malformed_json_and_a_rate_limit_do_not_raise_and_set_the_next_wait():
    rig = Rig()
    rig.answer = (200, {}, b"<html>")
    assert rig.poll() is None
    rig.answer = (429, {"retry-after": "900"}, b"{}")
    assert rig.poll() == 900
    rig.serve(R_OLD)
    rig.poll()
    assert rig.client.sent == []


def test_a_rate_limit_wait_never_goes_below_the_poll_interval():
    assert releases.next_delay(None, 600) == 600
    assert releases.next_delay(30, 600) == 600
    assert releases.next_delay(900, 600) == 900


def test_the_poll_interval_is_never_under_a_minute():
    assert releases.clamp_poll(5) == 60
    assert releases.clamp_poll(600) == 600


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
