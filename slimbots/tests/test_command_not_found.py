from slimbots import Bot, CommandNotFound
from slimbots.testing import FakeAsyncClient


async def test_a_stray_prefix_stays_silent_with_no_handler_registered():
    client = FakeAsyncClient(me_id="bot-1")
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    client.respond(
        "GET", "/members",
        [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}],
    )
    bot = Bot(prefix="!")
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    bot.me_id = "bot-1"
    await bot.space.refresh_members()
    client.calls.clear()

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!nonsense"})
    assert client.calls == []
    assert client.sent == []


async def test_a_registered_handler_receives_ctx_and_a_command_not_found_error():
    client = FakeAsyncClient(me_id="bot-1")
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    client.respond(
        "GET", "/members",
        [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}],
    )
    bot = Bot(prefix="!")
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    bot.me_id = "bot-1"
    await bot.space.refresh_members()

    seen = []

    @bot.event
    async def on_command_not_found(ctx, error):
        seen.append((ctx.invoked_with, error))

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!nonsense arg"})
    assert len(seen) == 1
    invoked_with, error = seen[0]
    assert invoked_with == "nonsense"
    assert isinstance(error, CommandNotFound)
    assert error.invoked_with == "nonsense"
    assert "nonsense" in str(error)


async def test_a_real_command_never_fires_command_not_found():
    client = FakeAsyncClient(me_id="bot-1")
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    client.respond(
        "GET", "/members",
        [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}],
    )
    bot = Bot(prefix="!")
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    bot.me_id = "bot-1"
    await bot.space.refresh_members()

    seen = []

    @bot.event
    async def on_command_not_found(ctx, error):
        seen.append(error)

    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!ping"})
    assert seen == []
    assert client.sent[-1]["content"] == "pong"
