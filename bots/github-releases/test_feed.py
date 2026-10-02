#!/usr/bin/env python3
"""The GitHub list fetch with its transport faked: ETags, rate limits, bad bodies; run directly: python3 test_feed.py."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import feed  # noqa: E402


class Transport:
    """Hands back queued (status, headers, body) answers and records the requests it was given."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


R1 = {"id": 1, "tag_name": "client-v1.0.0"}


def ok(releases, etag='W/"a"'):
    return 200, {"etag": etag}, json.dumps(releases).encode()


def make(*answers, token=None, now=1000.0):
    transport = Transport(*answers)
    return feed.Feed("Slim-m-org/slim-m", token=token, transport=transport, clock=lambda: now), transport


def test_a_good_answer_returns_the_list_and_asks_for_the_right_url():
    f, t = make(ok([R1]))
    result = f.fetch()
    assert (result.status, result.releases) == ("ok", [R1])
    assert t.requests[0][0] == "https://api.github.com/repos/Slim-m-org/slim-m/releases?per_page=30"
    assert t.requests[0][1]["accept"] == "application/vnd.github+json"
    assert "authorization" not in t.requests[0][1]


def test_a_token_goes_in_the_authorization_header_only():
    f, t = make(ok([]), token="ghp_secret")
    f.fetch()
    assert t.requests[0][1]["authorization"] == "Bearer ghp_secret"
    assert "ghp_secret" not in t.requests[0][0]


def test_an_accepted_etag_is_sent_back_and_a_304_is_unchanged():
    f, t = make(ok([R1]), (304, {}, b""))
    f.accept(f.fetch())
    result = f.fetch()
    assert t.requests[1][1]["if-none-match"] == 'W/"a"'
    assert (result.status, result.releases) == ("unchanged", [])


def test_an_etag_not_accepted_is_not_sent_so_an_unposted_release_is_refetched():
    f, t = make(ok([R1]), ok([R1]))
    f.fetch()
    f.fetch()
    assert "if-none-match" not in t.requests[1][1]


def test_a_429_with_retry_after_waits_that_long():
    f, _ = make((429, {"retry-after": "120"}, b"{}"))
    result = f.fetch()
    assert (result.status, result.wait) == ("limited", 120)


def test_a_403_with_no_remaining_waits_until_the_reset_time():
    f, _ = make((403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1900"}, b"{}"), now=1000.0)
    result = f.fetch()
    assert (result.status, result.wait) == ("limited", 900)


def test_a_wait_is_capped_at_an_hour():
    f, _ = make((429, {"retry-after": "999999"}, b"{}"))
    assert f.fetch().wait == 3600


def test_a_403_that_is_not_a_rate_limit_is_just_an_error():
    f, _ = make((403, {"x-ratelimit-remaining": "40"}, b'{"message":"forbidden"}'))
    result = f.fetch()
    assert result.status == "error" and "403" in result.detail


def test_invalid_json_and_a_non_list_are_errors_not_crashes():
    for body in (b"<html>oops", b'{"message":"x"}', b""):
        f, _ = make((200, {}, body))
        assert f.fetch().status == "error"


def test_a_dropped_connection_and_a_500_are_errors():
    f, _ = make(OSError("down"), (502, {}, b"bad gateway"))
    assert f.fetch().status == "error"
    assert f.fetch().status == "error"


def test_entries_that_are_not_objects_with_an_id_and_tag_are_dropped():
    f, _ = make(ok([1, "x", {"id": 2}, {"id": 3, "tag_name": "client-v1.0.0"}]))
    assert f.fetch().releases == [{"id": 3, "tag_name": "client-v1.0.0"}]


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
