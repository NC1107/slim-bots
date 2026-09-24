import asyncio
import os
import signal
import urllib.error

import pytest

from slimbots.lifecycle import guard_handler, run_with_shutdown


def test_guard_handler_returns_the_function_result():
    assert guard_handler(lambda x: x + 1, 1) == 2


def test_guard_handler_swallows_a_generic_error():
    def boom():
        raise ValueError("bad message")

    assert guard_handler(boom) is None


def test_guard_handler_reports_through_on_error():
    seen = []
    guard_handler(lambda: (_ for _ in ()).throw(RuntimeError("nope")), on_error=seen.append)
    assert len(seen) == 1
    assert isinstance(seen[0], RuntimeError)


def test_guard_handler_swallows_a_403():
    err = urllib.error.HTTPError("https://x", 403, "forbidden", {}, None)

    def boom():
        raise err

    assert guard_handler(boom) is None


def test_guard_handler_reraises_a_401():
    err = urllib.error.HTTPError("https://x", 401, "unauthorized", {}, None)

    def boom():
        raise err

    with pytest.raises(urllib.error.HTTPError) as excinfo:
        guard_handler(boom)
    assert excinfo.value.code == 401


async def test_run_with_shutdown_returns_the_coroutine_result_normally():
    async def work():
        return 42

    assert await run_with_shutdown(work) == 42


async def test_run_with_shutdown_exits_cleanly_on_sigterm():
    async def work():
        await asyncio.sleep(10)
        return "never"

    loop = asyncio.get_running_loop()
    loop.call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)

    result = await asyncio.wait_for(run_with_shutdown(work), timeout=2)
    assert result == 0


async def test_run_with_shutdown_reraises_a_cancellation_it_did_not_request():
    async def work():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await run_with_shutdown(work)
