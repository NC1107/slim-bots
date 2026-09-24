import asyncio

import pytest

from slimbots import Bot
from slimbots.context import Context
from slimbots.testing import FakeAsyncClient


@pytest.fixture
def client():
    return FakeAsyncClient(me_id="bot-1")


@pytest.fixture
async def bot(client):
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    b = Bot(prefix="!", channels={"c1"})
    b.client = client
    b.space = Space(client)
    b.authors = AuthorFilter(client, space=b.space, ignore_bots=True)
    b.me_id = "bot-1"
    client.respond(
        "GET", "/members",
        [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}],
    )
    await b.space.refresh_members()
    return b


async def test_wait_for_returns_the_first_matching_dispatch(bot):
    async def fire_soon():
        await asyncio.sleep(0.01)
        await bot._handle_frame({"type": "message.created", "channel_id": "c1", "message": {"id": "m1", "content": "go", "author_id": "u1"}})

    asyncio.ensure_future(fire_soon())
    message = await bot.wait_for("on_raw_message", timeout=1)
    assert message["content"] == "go"


async def test_wait_for_applies_check_and_skips_a_non_matching_dispatch(bot):
    async def fire_two():
        await bot._handle_frame({"type": "message.created", "channel_id": "c1", "message": {"id": "m1", "content": "no", "author_id": "u1"}})
        await bot._handle_frame({"type": "message.created", "channel_id": "c1", "message": {"id": "m2", "content": "yes", "author_id": "u1", "channel_id": "c1"}})

    asyncio.ensure_future(fire_two())
    message = await bot.wait_for("on_raw_message", check=lambda m: m["content"] == "yes", timeout=1)
    assert message["content"] == "yes"


async def test_wait_for_times_out_and_cleans_up_its_listener(bot):
    with pytest.raises(asyncio.TimeoutError):
        await bot.wait_for("on_raw_message", timeout=0.02)
    assert bot._listeners.get("on_raw_message", []) == []


async def test_ctx_confirm_yes_returns_true(bot, client):
    ctx = Context(bot=bot, message={"id": "m1"}, author=bot.space.members["u1"], channel_id="c1")

    async def reply_yes():
        await asyncio.sleep(0.01)
        await bot._handle_frame({"type": "message.created", "channel_id": "c1", "message": {"id": "m2", "content": "yes", "author_id": "u1", "channel_id": "c1"}})

    asyncio.ensure_future(reply_yes())
    assert await ctx.confirm("Clear it?", timeout=1) is True
    assert "(yes/no)" in client.sent[0]["content"]


async def test_ctx_confirm_no_returns_false(bot):
    ctx = Context(bot=bot, message={"id": "m1"}, author=bot.space.members["u1"], channel_id="c1")

    async def reply_no():
        await asyncio.sleep(0.01)
        await bot._handle_frame({"type": "message.created", "channel_id": "c1", "message": {"id": "m2", "content": "no", "author_id": "u1", "channel_id": "c1"}})

    asyncio.ensure_future(reply_no())
    assert await ctx.confirm("Clear it?", timeout=1) is False


async def test_ctx_confirm_ignores_a_reply_from_someone_else(bot):
    ctx = Context(bot=bot, message={"id": "m1"}, author=bot.space.members["u1"], channel_id="c1")

    async def reply_from_wrong_user():
        await bot._handle_frame({"type": "message.created", "channel_id": "c1", "message": {"id": "m2", "content": "yes", "author_id": "someone-else", "channel_id": "c1"}})
        await asyncio.sleep(0.01)
        await bot._handle_frame({"type": "message.created", "channel_id": "c1", "message": {"id": "m3", "content": "no", "author_id": "u1", "channel_id": "c1"}})

    asyncio.ensure_future(reply_from_wrong_user())
    assert await ctx.confirm("Clear it?", timeout=1) is False


async def test_ctx_confirm_times_out_to_false(bot):
    ctx = Context(bot=bot, message={"id": "m1"}, author=bot.space.members["u1"], channel_id="c1")
    assert await ctx.confirm("Clear it?", timeout=0.02) is False
