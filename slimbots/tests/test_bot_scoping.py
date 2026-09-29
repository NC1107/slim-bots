import pytest

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.models import Channel
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MEMBER = {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}


async def make_bot(monkeypatch, **kwargs):
    monkeypatch.delenv("SLIMM_PREFIX", raising=False)
    monkeypatch.delenv("SLIMM_LISTEN_VOICE_CHATS", raising=False)
    client = FakeAsyncClient()
    bot = Bot(**kwargs)
    bot.client, bot.space, bot.me_id, bot.username = client, Space(client), "bot-1", "jellyfin"
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    client.respond("GET", "/members", [MEMBER])
    await bot.space.refresh_members()
    bot.space.channels = {
        "text": Channel({"id": "text", "name": "general"}),
        "voice": Channel({"id": "voice", "name": "call", "kind": "voice"}),
    }
    return bot, client


async def say(bot, content, channel="text"):
    await bot.process_message({"id": "m", "author_id": "u1", "channel_id": channel, "content": content})


def frame(channel_id):
    message = {"id": "m1", "author_id": "u1", "channel_id": channel_id, "content": "!ping"}
    return {"type": "message.created", "channel_id": channel_id, "message": message}


def record_raw_messages(bot):
    seen = []

    @bot.event
    async def on_raw_message(message):
        seen.append(message["channel_id"])

    return seen


async def test_voice_chat_is_dropped_by_default_when_channels_are_scoped(monkeypatch):
    bot, _ = await make_bot(monkeypatch, channels={"text"})
    seen = record_raw_messages(bot)
    await bot._handle_frame(frame("voice"))
    assert seen == []


async def test_listen_voice_chats_accepts_a_voice_channel_but_not_another_text_channel(monkeypatch):
    bot, _ = await make_bot(monkeypatch, channels={"text"}, listen_voice_chats=True)
    seen = record_raw_messages(bot)
    await bot._handle_frame(frame("voice"))
    await bot._handle_frame(frame("other-text"))
    assert seen == ["voice"]


async def test_env_var_turns_voice_chat_listening_on(monkeypatch):
    monkeypatch.setenv("SLIMM_LISTEN_VOICE_CHATS", "1")
    assert Bot(channels={"text"}).listen_voice_chats is True


async def test_prefix_env_var_overrides_the_constructor_default(monkeypatch):
    monkeypatch.setenv("SLIMM_PREFIX", "j!")
    assert Bot(prefix="!").prefix == "j!"


async def test_custom_prefix_ignores_the_old_one(monkeypatch):
    bot, client = await make_bot(monkeypatch, prefix="j!")

    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    await say(bot, "!ping")
    assert client.sent == []
    await say(bot, "j!ping")
    assert client.sent[-1]["content"] == "pong"


async def test_mention_addresses_one_bot_in_a_shared_channel(monkeypatch):
    bot, client = await make_bot(monkeypatch)

    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    await say(bot, "@otherbot ping")
    assert client.sent == []
    await say(bot, "@Jellyfin ping")
    assert client.sent[-1]["content"] == "pong"


async def test_mention_commands_can_be_switched_off(monkeypatch):
    bot, client = await make_bot(monkeypatch, mention_commands=False)

    @bot.command()
    async def ping(ctx):
        await ctx.reply("pong")

    await say(bot, "@jellyfin ping")
    assert client.sent == []


async def test_help_names_the_bot_and_both_ways_to_call_it(monkeypatch):
    bot, client = await make_bot(monkeypatch, prefix="j!")
    await say(bot, "j!help")
    header = client.sent[-1]["content"].splitlines()[0]
    assert header == "**jellyfin commands** (prefix `j!`, or `@jellyfin <command>`)"


@pytest.mark.parametrize("username", [None, ""])
async def test_help_falls_back_before_the_username_is_known(monkeypatch, username):
    bot, client = await make_bot(monkeypatch)
    bot.username = username
    await say(bot, "!help")
    assert client.sent[-1]["content"].splitlines()[0] == "**Commands** (prefix `!`)"
