import pytest

from slimbots import Bot, Duration, TimeOfDay
from slimbots.commands import build_help_text
from slimbots.registration import registration_body
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
    client.respond("GET", "/members", [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}])
    await b.space.refresh_members()
    return b


def _wire_remind(bot):
    @bot.group(name="remind", help="Schedule a reminder")
    async def remind(ctx, rest: str = ""):
        await ctx.reply("try `!remind in|at|every ...`")

    @remind.command(name="in", usage="<duration> <text>")
    async def remind_in(ctx, duration: Duration, text: str):
        await ctx.reply(f"in {int(duration)}s: {text}")

    @remind.command(name="at", usage="<HH:MM> <text>")
    async def remind_at(ctx, when: TimeOfDay, text: str):
        await ctx.reply(f"at {when.hour:02d}:{when.minute:02d}: {text}")

    return remind


async def send(bot, client, content):
    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": content})
    return client.sent[-1]["content"]


async def test_subcommand_dispatch_by_first_token(bot, client):
    _wire_remind(bot)
    assert await send(bot, client, "!remind in 10m buy milk") == "in 600s: buy milk"


async def test_a_second_subcommand_on_the_same_group(bot, client):
    _wire_remind(bot)
    assert await send(bot, client, "!remind at 09:00 stand up") == "at 09:00: stand up"


async def test_unrecognised_subcommand_falls_to_the_group_handler(bot, client):
    _wire_remind(bot)
    assert await send(bot, client, "!remind bogus") == "try `!remind in|at|every ...`"


async def test_bare_group_invocation_falls_to_the_group_handler(bot, client):
    _wire_remind(bot)
    assert await send(bot, client, "!remind") == "try `!remind in|at|every ...`"


async def test_bad_subcommand_argument_is_a_reply_not_a_crash(bot, client):
    _wire_remind(bot)
    reply = await send(bot, client, "!remind in soon buy milk")
    assert "duration" in reply


async def test_group_level_check_gates_every_subcommand(bot, client):
    remind = _wire_remind(bot)
    remind.requires = 1 << 30  # a bit no test member holds

    reply = await send(bot, client, "!remind in 10m buy milk")
    assert "permission" in reply


def test_help_lists_the_group_with_its_subcommands_joined():
    bot = Bot(help_command=False)
    _wire_remind(bot)
    text = build_help_text(bot)
    assert "!remind in|at ..." in text


def test_help_for_the_group_lists_each_subcommand_name():
    bot = Bot(help_command=False)
    _wire_remind(bot)
    text = build_help_text(bot, command_name="remind")
    assert "subcommands: in, at" in text


def test_help_for_one_subcommand_shows_its_own_usage():
    bot = Bot(help_command=False)
    _wire_remind(bot)
    text = build_help_text(bot, command_name="remind in")
    assert "!remind in <duration> <text>" in text


def test_help_for_an_unknown_subcommand_says_so():
    bot = Bot(help_command=False)
    _wire_remind(bot)
    text = build_help_text(bot, command_name="remind bogus")
    assert "no subcommand called `bogus`" in text


def test_registration_flattens_a_group_to_one_entry():
    bot = Bot(help_command=False)
    _wire_remind(bot)
    body = registration_body("!", bot.unique_commands())
    assert len(body["commands"]) == 1
    assert body["commands"][0]["name"] == "remind"
    assert "in" in body["commands"][0]["usage"]
