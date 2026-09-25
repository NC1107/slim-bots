"""A real deadlock: a command's wait_for() used to block the very frame that would resolve it, since the
frame loop awaited process_message() to completion first. Drives _handle_frame sequentially, like the real gateway loop - not process_message directly, which is what let the old tests miss this."""

import asyncio

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]


def frame(content, msg_id):
    return {
        "type": "message.created", "channel_id": "c1",
        "message": {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content},
    }


async def a_bot():
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    bot = Bot(prefix="!")
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    bot.me_id = "bot-1"
    await bot.space.refresh_members()
    return bot, client


async def test_wait_for_inside_a_command_resolves_from_the_next_frame_not_a_timeout():
    bot, client = await a_bot()
    seen = []

    @bot.command()
    async def pick(ctx):
        reply = await bot.wait_for("on_raw_message", check=lambda m: m.get("content") == "1", timeout=2)
        seen.append(reply)
        await ctx.reply("got it")

    await bot._handle_frame(frame("!pick", "m1"))
    await asyncio.sleep(0.01)  # let the command's own task actually start and register its wait_for listener
    await bot._handle_frame(frame("1", "m2"))
    await asyncio.sleep(0.01)  # let the command task pick the reply back up and finish
    assert seen, "wait_for never saw the second frame - the frame loop was blocked inside the first command"
    assert client.sent[-1]["content"] == "got it"


async def test_a_second_command_in_a_different_channel_is_not_blocked_by_the_first_ones_wait_for():
    """pick()'s own wait_for is given a long timeout it never resolves - if the frame loop were still
    blocking on it, reading and answering the unrelated !ping frame would hang for that whole timeout too."""
    bot, client = await a_bot()

    @bot.command()
    async def pick(ctx):
        await bot.wait_for("on_raw_message", check=lambda m: m.get("content") == "1", timeout=30)
        await ctx.reply("picked")

    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    def other_channel_frame(content, msg_id):
        return {
            "type": "message.created", "channel_id": "c2",
            "message": {"id": msg_id, "author_id": "u1", "channel_id": "c2", "content": content},
        }

    await bot._handle_frame(frame("!pick", "m1"))
    await asyncio.sleep(0.01)

    async def send_ping_and_wait():
        await bot._handle_frame(other_channel_frame("!ping", "m2"))
        await asyncio.sleep(0.01)

    await asyncio.wait_for(send_ping_and_wait(), timeout=1.0)
    assert client.sent[-1]["content"] == "pong"
