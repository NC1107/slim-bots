#!/usr/bin/env python3
"""A poster fetch that fails returns None and says why on stderr, instead of failing silently."""

import contextlib
import io
import os
import sys
import urllib.error
import urllib.request

os.environ.setdefault("JELLYFIN_URL", "https://fake-jellyfin.invalid")
os.environ.setdefault("JELLYFIN_API_KEY", "fake-key")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as jellyfin  # noqa: E402,F401
import jellyfin_core  # noqa: E402


def fetch_with(error):
    def refuse(*_args, **_kwargs):
        raise error

    original = urllib.request.urlopen
    urllib.request.urlopen = refuse
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            result = jellyfin_core.jf_get_bytes("/Items/i1/Images/Primary?api_key=secret")
    finally:
        urllib.request.urlopen = original
    return result, stderr.getvalue()


def test_a_network_failure_returns_none_and_is_logged_without_the_query():
    result, logged = fetch_with(urllib.error.URLError("no route"))
    assert result is None
    assert "/Items/i1/Images/Primary failed: URLError" in logged
    assert "secret" not in logged


def test_a_timeout_returns_none_and_is_logged():
    result, logged = fetch_with(TimeoutError("timed out"))
    assert result is None and "TimeoutError" in logged


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
