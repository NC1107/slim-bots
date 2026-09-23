"""Retrying a REST call, but only when the outcome is genuinely uncertain.

A rejected request (a 4xx other than 429) is a certain outcome: the server
looked at it and said no, and sending the same thing again gets the same no.
Retrying that would be wrong, not just wasteful. What is worth retrying is a
call whose outcome we never actually learned: a network error before any
response came back, a 5xx (the server broke, not the request), or a 429
(rate limited). slim-m's plain rate limit carries no `Retry-After`, so a
fixed or exponential backoff is the only option - there is nothing to read
that would do better.
"""

import time
import urllib.error


def is_retryable(err):
    """Whether `err`, raised by `urllib.request.urlopen`, is worth another try."""
    if isinstance(err, urllib.error.HTTPError):
        return err.code == 429 or err.code >= 500
    # A URLError with no `code` attribute never reached a server at all.
    return isinstance(err, urllib.error.URLError)


def call_with_retry(fn, *, retries=5, base_delay=0.5, max_delay=8.0, sleep=time.sleep):
    """Calls `fn()`, retrying up to `retries` times on a retryable failure.

    Backoff doubles from `base_delay`, capped at `max_delay`. `fn` must be
    safe to call more than once with the same effect - true of any call
    built on an idempotent id, and never true of a plain uuid4() per call.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except (urllib.error.HTTPError, urllib.error.URLError) as err:
            if attempt >= retries or not is_retryable(err):
                raise
            sleep(min(base_delay * (2**attempt), max_delay))
            attempt += 1
