import asyncio
import os
import signal

import pytest

from slimbots import Bot
from slimbots.http import ApiError


async def test_background_task_is_held_strongly_while_pending():
    bot = Bot()
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(0.05)

    task = bot.background(slow())
    await started.wait()
    assert task in bot._background_tasks
    await task
    assert task not in bot._background_tasks


async def test_background_exception_cancels_start_and_records_the_error(monkeypatch):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    bot = Bot()

    async def boom():
        await asyncio.sleep(0.01)
        raise RuntimeError("kaboom")

    async def fake_run_forever():
        bot.background(boom(), name="boom")
        await asyncio.sleep(10)
        return 0

    monkeypatch.setattr(bot, "_run_forever", fake_run_forever)
    with pytest.raises(asyncio.CancelledError):
        await bot.start()
    assert isinstance(bot._fatal_error, RuntimeError)


async def test_a_401_in_a_background_task_stays_terminal(monkeypatch):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    bot = Bot()

    async def revoked():
        raise ApiError(401, {"error": "revoked"})

    async def fake_run_forever():
        bot.background(revoked())
        await asyncio.sleep(10)
        return 0

    monkeypatch.setattr(bot, "_run_forever", fake_run_forever)
    with pytest.raises(asyncio.CancelledError):
        await bot.start()
    assert isinstance(bot._fatal_error, ApiError)


async def test_shutdown_cancels_a_still_running_background_task(monkeypatch):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    bot = Bot()
    cancelled = []

    async def long_runner():
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def fake_run_forever():
        bot.background(long_runner())
        await asyncio.sleep(100)
        return 0

    monkeypatch.setattr(bot, "_run_forever", fake_run_forever)
    task = asyncio.ensure_future(bot.start())
    await asyncio.sleep(0.05)
    os.kill(os.getpid(), signal.SIGTERM)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert cancelled == [True]
    assert bot._background_tasks == set()
    assert bot._fatal_error is None


def test_run_converts_a_clean_shutdown_cancellation_to_exit_0(monkeypatch):
    bot = Bot()

    async def fake_start(*, url=None, token=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(bot, "start", fake_start)
    assert bot.run() == 0


def test_run_converts_a_fatal_background_cancellation_to_exit_1(monkeypatch):
    bot = Bot()
    bot._fatal_error = RuntimeError("boom")

    async def fake_start(*, url=None, token=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(bot, "start", fake_start)
    assert bot.run() == 1
