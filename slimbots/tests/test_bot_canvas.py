import pytest

from slimbots import Bot, Canvas
from slimbots.testing import FakeAsyncClient


async def test_bot_canvas_targets_the_given_channel_not_a_command_channel():
    bot = Bot(prefix="!")
    bot.client = FakeAsyncClient()
    canvas = bot.canvas("some-other-channel")
    assert isinstance(canvas, Canvas)
    assert canvas.channel_id == "some-other-channel"


async def test_bot_canvas_is_wired_for_live_gateway_signals():
    bot = Bot(prefix="!")
    bot.client = FakeAsyncClient()
    canvas = bot.canvas("c1")
    assert canvas._bot is bot


def test_bot_canvas_refuses_without_an_open_connection():
    bot = Bot(prefix="!")
    with pytest.raises(AssertionError):
        bot.canvas("c1")
