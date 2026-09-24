import pytest

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MEMBERS = [
    {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []},
    {"id": "u2", "username": "sam", "display_name": "Sam", "is_bot": False, "is_webhook": False, "role_ids": []},
]


@pytest.fixture
async def bot():
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    b = Bot(prefix="!")
    b.client = client
    b.space = Space(client)
    b.authors = AuthorFilter(client, space=b.space, ignore_bots=True)
    b.me_id = "bot-1"
    await b.space.refresh_members()
    return b, client


def message(author_id, channel_id, content, msg_id="m1"):
    return {"id": msg_id, "author_id": author_id, "channel_id": channel_id, "content": content}


async def test_per_user_cooldown_is_the_default_and_does_not_block_a_different_user(bot):
    b, client = bot

    @b.command(cooldown=100)
    async def daily(ctx):
        await ctx.reply("granted")

    await b.process_message(message("u1", "c1", "!daily", "m1"))
    await b.process_message(message("u2", "c1", "!daily", "m2"))
    assert client.sent[0]["content"] == "granted"
    assert client.sent[1]["content"] == "granted"


async def test_channel_bucket_blocks_a_different_user_in_the_same_channel(bot):
    b, client = bot

    @b.command(cooldown=100, cooldown_bucket="channel")
    async def daily(ctx):
        await ctx.reply("granted")

    await b.process_message(message("u1", "c1", "!daily", "m1"))
    await b.process_message(message("u2", "c1", "!daily", "m2"))
    assert client.sent[0]["content"] == "granted"
    assert "try again" in client.sent[1]["content"]


async def test_channel_bucket_does_not_block_a_different_channel(bot):
    b, client = bot

    @b.command(cooldown=100, cooldown_bucket="channel")
    async def daily(ctx):
        await ctx.reply("granted")

    await b.process_message(message("u1", "c1", "!daily", "m1"))
    await b.process_message(message("u1", "c2", "!daily", "m2"))
    assert client.sent[0]["content"] == "granted"
    assert client.sent[1]["content"] == "granted"


async def test_deployment_bucket_blocks_every_user_in_every_channel(bot):
    b, client = bot

    @b.command(cooldown=100, cooldown_bucket="deployment")
    async def daily(ctx):
        await ctx.reply("granted")

    await b.process_message(message("u1", "c1", "!daily", "m1"))
    await b.process_message(message("u2", "c2", "!daily", "m2"))
    assert client.sent[0]["content"] == "granted"
    assert "try again" in client.sent[1]["content"]


async def test_group_subcommands_accept_a_cooldown_bucket_too(bot):
    b, client = bot

    @b.group()
    async def shop(ctx):
        await ctx.reply("usage: !shop buy")

    @shop.command(cooldown=100, cooldown_bucket="channel")
    async def buy(ctx):
        await ctx.reply("bought")

    await b.process_message(message("u1", "c1", "!shop buy", "m1"))
    await b.process_message(message("u2", "c1", "!shop buy", "m2"))
    assert client.sent[0]["content"] == "bought"
    assert "try again" in client.sent[1]["content"]


def test_an_unknown_bucket_name_is_rejected_up_front():
    b = Bot(prefix="!")
    with pytest.raises(ValueError, match="cooldown_bucket"):
        @b.command(cooldown=10, cooldown_bucket="server")
        async def oops(ctx):
            pass
