"""Commands declared in a `from __future__ import annotations` module must convert like eager ones."""

from __future__ import annotations

import pytest

from slimbots import Bot, Duration
from slimbots.models import Member
from slimbots.testing import FakeAsyncClient

_MEMBER = {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}


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
    client.respond("GET", "/members", [_MEMBER])
    await b.space.refresh_members()
    return b


async def send(bot, client, content):
    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": content})
    return client.sent[-1]["content"]


async def test_last_str_param_takes_the_rest_of_the_line(bot, client):
    @bot.command(name="watch")
    async def watch(ctx, query: str = ""):
        await ctx.reply(f"query={query!r}")

    assert await send(bot, client, "!watch two words here") == "query='two words here'"


async def test_str_rest_after_a_typed_param(bot, client):
    @bot.command(name="rep")
    async def rep(ctx, times: int, text: str):
        await ctx.reply(f"{times}x {text}")

    assert await send(bot, client, "!rep 3 hello there") == "3x hello there"


async def test_int_and_duration_params_are_converted(bot, client):
    @bot.command(name="wait")
    async def wait(ctx, count: int, span: Duration):
        await ctx.reply(f"{type(count).__name__} {int(span)}")

    assert await send(bot, client, "!wait 4 2m") == "int 120"


async def test_a_bad_int_is_a_reply_not_a_raw_string(bot, client):
    @bot.command(name="num")
    async def num(ctx, count: int):
        await ctx.reply("ran")

    assert "whole number" in await send(bot, client, "!num many")


async def test_member_param_is_resolved(bot, client):
    @bot.command(name="who")
    async def who(ctx, target: Member):
        await ctx.reply(type(target).__name__)

    assert await send(bot, client, "!who nick") == "Member"


async def test_usage_marks_the_rest_param(bot):
    @bot.command(name="say")
    async def say(ctx, text: str):
        pass

    assert bot.commands["say"].usage == "<text...>"


async def test_an_unresolvable_annotation_degrades_to_a_plain_token(bot, client):
    @bot.command(name="odd")
    async def odd(ctx, thing: NoSuchType):  # type: ignore[name-defined]  # noqa: F821
        await ctx.reply(f"thing={thing!r}")

    assert await send(bot, client, "!odd a b") == "thing='a'"
