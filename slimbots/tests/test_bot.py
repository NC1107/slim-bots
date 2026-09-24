import asyncio

import pytest

from slimbots import Bot, Member, Permissions
from slimbots.http import ApiError
from slimbots.testing import FakeAsyncClient


@pytest.fixture
def client():
    return FakeAsyncClient(me_id="bot-1")


@pytest.fixture
async def bot(client):
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    b = Bot(prefix="!")
    b.client = client
    b.space = Space(client)
    b.authors = AuthorFilter(client, space=b.space, ignore_bots=True)
    b.me_id = "bot-1"
    client.respond(
        "GET",
        "/members",
        [
            {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []},
            {"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []},
        ],
    )
    await b.space.refresh_members()
    return b


async def test_argument_conversion_int_and_member(bot, client):
    @bot.command()
    async def give(ctx, amount: int, member: Member):
        await ctx.reply(f"gave {amount} to {member.display_name}")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!give 5 @nick"})
    assert client.sent[-1]["content"] == "gave 5 to Nick"


async def test_bad_argument_is_a_reply_not_a_crash(bot, client):
    @bot.command()
    async def give(ctx, amount: int, member: Member):
        await ctx.reply("should not run")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!give notanumber @nick"})
    assert "whole number" in client.sent[-1]["content"]


async def test_missing_required_argument(bot, client):
    @bot.command()
    async def give(ctx, amount: int, member: Member):
        await ctx.reply("should not run")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!give"})
    assert "missing a value" in client.sent[-1]["content"]


async def test_rest_of_message_string_argument(bot, client):
    @bot.command()
    async def say(ctx, text: str):
        await ctx.reply(f"echo: {text}")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!say hello there world"})
    assert client.sent[-1]["content"] == "echo: hello there world"


async def test_cooldown_blocks_second_call(bot, client):
    @bot.command(cooldown=100)
    async def daily(ctx):
        await ctx.reply("chips granted")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!daily"})
    assert client.sent[-1]["content"] == "chips granted"

    await bot.process_message({"id": "m2", "author_id": "u1", "channel_id": "c1", "content": "!daily"})
    assert "try again" in client.sent[-1]["content"]


async def test_bot_authors_are_ignored_by_default(bot, client):
    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    before = len(client.sent)
    await bot.process_message({"id": "m1", "author_id": "bot-2", "channel_id": "c1", "content": "!ping"})
    assert len(client.sent) == before


async def test_ignore_bots_can_be_disabled(client):
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    bot = Bot(prefix="!", ignore_bots=False)
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=False)
    bot.me_id = "bot-1"
    client.respond(
        "GET",
        "/members",
        [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []}],
    )
    await bot.space.refresh_members()

    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    await bot.process_message({"id": "m1", "author_id": "bot-2", "channel_id": "c1", "content": "!ping"})
    assert client.sent[-1]["content"] == "pong"


async def test_a_bot_never_answers_itself(bot, client):
    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    before = len(client.sent)
    await bot.process_message({"id": "m1", "author_id": "bot-1", "channel_id": "c1", "content": "!ping"})
    assert len(client.sent) == before


async def test_permission_gate_denies_without_roles_loaded(bot, client):
    @bot.command(requires=Permissions.MANAGE_ROLES)
    async def promote(ctx, member: Member):
        await ctx.reply("promoted")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!promote @nick"})
    assert "do not have permission" in client.sent[-1]["content"]


async def test_permission_gate_allows_a_role_holder(client):
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    bot = Bot(prefix="!")
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    bot.me_id = "bot-1"
    client.respond("GET", "/roles", [{"id": "r1", "name": "mod", "permissions": int(Permissions.MANAGE_ROLES), "is_everyone": False}])
    client.respond(
        "GET",
        "/members",
        [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": ["r1"]}],
    )
    await bot.space.refresh_roles()
    await bot.space.refresh_members()

    @bot.command(requires=Permissions.MANAGE_ROLES)
    async def promote(ctx, member: Member):
        await ctx.reply("promoted")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!promote @nick"})
    assert client.sent[-1]["content"] == "promoted"


async def test_help_lists_registered_commands(bot, client):
    @bot.command(help="says hi")
    async def hello(ctx):
        await ctx.reply("hi")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!help"})
    assert "!hello" in client.sent[-1]["content"]
    assert "says hi" in client.sent[-1]["content"]


async def test_forbidden_from_the_bots_own_action_replies_clearly(bot, client):
    @bot.command()
    async def grant(ctx, member: Member):
        await bot.space.grant_role(member, "r-missing")

    client.respond("PUT", "/members/u1/roles/r-missing", ApiError(403, {"error": "cannot grant a permission you do not hold"}))
    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!grant @nick"})
    assert "can't do that" in client.sent[-1]["content"]
    assert "cannot grant a permission" in client.sent[-1]["content"]


async def test_global_check_can_refuse_before_any_command_runs(bot, client):
    @bot.check
    async def deny_everyone(ctx):
        return "not right now"

    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!ping"})
    assert client.sent[-1]["content"] == "not right now"


async def test_member_conversion_falls_back_to_a_live_fetch_by_id(bot, client):
    client.respond(
        "GET",
        "/users/u9",
        {"id": "u9", "username": "freshuser", "display_name": "Fresh", "is_bot": False, "is_webhook": False, "role_ids": []},
    )

    @bot.command()
    async def whois(ctx, member: Member):
        await ctx.reply(f"found {member.display_name}")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!whois u9"})
    assert client.sent[-1]["content"] == "found Fresh"


async def test_a_token_revocation_propagates_instead_of_being_swallowed(bot, client):
    @bot.command()
    async def whoami(ctx):
        raise ApiError(401, {"error": "revoked"})

    with pytest.raises(ApiError):
        await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!whoami"})
