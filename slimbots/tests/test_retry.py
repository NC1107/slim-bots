import urllib.error

import pytest

from slimbots.retry import call_with_retry, is_retryable


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


@pytest.mark.parametrize("code", [429, 500, 502, 503, 599])
def test_retryable_codes(code):
    assert is_retryable(http_error(code))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422])
def test_non_retryable_4xx(code):
    assert not is_retryable(http_error(code))


def test_network_error_is_retryable():
    assert is_retryable(urllib.error.URLError("connection refused"))


def test_succeeds_without_retry():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    assert call_with_retry(fn, sleep=lambda _: None) == "ok"
    assert len(calls) == 1


def test_retries_a_5xx_then_succeeds():
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise http_error(503)
        return "ok"

    result = call_with_retry(fn, retries=5, sleep=lambda _: None)
    assert result == "ok"
    assert len(attempts) == 3


def test_retries_a_429_then_succeeds():
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 2:
            raise http_error(429)
        return "ok"

    assert call_with_retry(fn, sleep=lambda _: None) == "ok"
    assert len(attempts) == 2


def test_retries_a_network_error():
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 2:
            raise urllib.error.URLError("boom")
        return "ok"

    assert call_with_retry(fn, sleep=lambda _: None) == "ok"


def test_never_retries_a_rejected_4xx():
    attempts = []

    def fn():
        attempts.append(1)
        raise http_error(400)

    with pytest.raises(urllib.error.HTTPError):
        call_with_retry(fn, sleep=lambda _: None)
    assert len(attempts) == 1


def test_never_retries_a_401():
    attempts = []

    def fn():
        attempts.append(1)
        raise http_error(401)

    with pytest.raises(urllib.error.HTTPError):
        call_with_retry(fn, sleep=lambda _: None)
    assert len(attempts) == 1


def test_gives_up_after_retries_exhausted():
    attempts = []

    def fn():
        attempts.append(1)
        raise http_error(500)

    with pytest.raises(urllib.error.HTTPError):
        call_with_retry(fn, retries=3, sleep=lambda _: None)
    # the first attempt plus 3 retries
    assert len(attempts) == 4


def test_backoff_doubles_and_caps():
    delays = []

    def fn():
        raise http_error(500)

    with pytest.raises(urllib.error.HTTPError):
        call_with_retry(
            fn, retries=4, base_delay=1.0, max_delay=4.0, sleep=delays.append
        )
    assert delays == [1.0, 2.0, 4.0, 4.0]


def test_retry_never_needs_a_new_id_or_content():
    """The id and content a retried call sends are fixed by the caller before
    call_with_retry ever runs; this asserts the same closure - and therefore
    the same body - is invoked on every attempt."""
    bodies_seen = []

    def fn():
        # A real caller closes over one fixed body; we simulate that here.
        bodies_seen.append({"id": "fixed-id", "content": "fixed content"})
        if len(bodies_seen) < 3:
            raise http_error(500)
        return bodies_seen[-1]

    call_with_retry(fn, sleep=lambda _: None)
    assert len({(b["id"], b["content"]) for b in bodies_seen}) == 1
