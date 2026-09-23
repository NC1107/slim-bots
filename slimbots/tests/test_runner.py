import urllib.error

import pytest

from slimbots.runner import run_forever


@pytest.mark.asyncio
async def test_stops_on_a_revoked_token(monkeypatch):
    async def fake_sleep(_):
        pytest.fail("should not sleep after a terminal 401")

    monkeypatch.setattr("slimbots.runner.asyncio.sleep", fake_sleep)

    async def attempt(reset_delay):
        raise urllib.error.HTTPError("u", 401, "e", {}, None)

    assert await run_forever(attempt) == 1


@pytest.mark.asyncio
async def test_retries_other_failures_with_backoff(monkeypatch):
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)
        if len(delays) >= 3:
            raise SystemExit("stop the loop")

    monkeypatch.setattr("slimbots.runner.asyncio.sleep", fake_sleep)

    async def attempt(reset_delay):
        raise ConnectionError("dropped")

    with pytest.raises(SystemExit):
        await run_forever(attempt, base_delay=1.0, max_delay=4.0)

    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_a_403_is_not_terminal(monkeypatch):
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)
        raise SystemExit("stop the loop")

    monkeypatch.setattr("slimbots.runner.asyncio.sleep", fake_sleep)

    async def attempt(reset_delay):
        raise urllib.error.HTTPError("u", 403, "e", {}, None)

    with pytest.raises(SystemExit):
        await run_forever(attempt)
    assert delays == [1.0]


@pytest.mark.asyncio
async def test_reset_delay_is_offered_to_the_attempt():
    calls = []

    async def attempt(reset_delay):
        calls.append(reset_delay)
        raise urllib.error.HTTPError("u", 401, "e", {}, None)

    await run_forever(attempt)
    assert len(calls) == 1
    calls[0]()  # must not raise
