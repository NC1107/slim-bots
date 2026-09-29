import logging

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.bot import CHANNEL_MISS_REFRESH_SECONDS
from slimbots.models import Channel
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MEMBER = {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}
TEXT = {"id": "text", "name": "general"}


def voice_dto(channel_id):
    return {"id": channel_id, "name": "call", "kind": "voice"}


async def make_bot(monkeypatch, listed=None):
    monkeypatch.delenv("SLIMM_PREFIX", raising=False)
    monkeypatch.delenv("SLIMM_LISTEN_VOICE_CHATS", raising=False)
    client = FakeAsyncClient()
    bot = Bot(channels={"text"}, listen_voice_chats=True)
    bot.client, bot.space, bot.me_id, bot.username = client, Space(client), "bot-1", "jellyfin"
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    client.respond("GET", "/members", [MEMBER])
    await bot.space.refresh_members()
    bot.space.channels = {"text": Channel(TEXT)}
    listed = [TEXT] if listed is None else listed
    client.respond("GET", "/channels", lambda *_a, **_k: list(listed))
    return bot, client, listed


def help_frame(channel_id):
    message = {"id": "m1", "author_id": "u1", "channel_id": channel_id, "content": "!help"}
    return {"type": "message.created", "channel_id": channel_id, "message": message}


async def drain(bot):
    while bot._background_tasks:
        await list(bot._background_tasks)[0]


def channel_fetches(client):
    return [c for c in client.calls if c[:2] == ("GET", "/channels")]


async def test_voice_channel_created_after_connect_gets_a_reply(monkeypatch):
    bot, client, _ = await make_bot(monkeypatch)
    await bot._handle_frame({"type": "channel.created", "channel": voice_dto("late")})
    await bot._handle_frame(help_frame("late"))
    await drain(bot)
    assert [m["channel_id"] for m in client.sent] == ["late"]


async def test_deleted_channel_stops_being_listened_to(monkeypatch):
    bot, client, _ = await make_bot(monkeypatch)
    await bot._handle_frame({"type": "channel.created", "channel": voice_dto("late")})
    await bot._handle_frame({"type": "channel.deleted", "channel_id": "late"})
    fetches_before = len(channel_fetches(client))
    await bot._handle_frame(help_frame("late"))
    await drain(bot)
    assert client.sent == []
    assert "late" not in bot.space.channels
    assert len(channel_fetches(client)) == fetches_before + 1


async def test_channel_updated_to_voice_is_picked_up(monkeypatch):
    bot, client, _ = await make_bot(monkeypatch)
    bot.space.channels["c"] = Channel({"id": "c", "name": "room", "kind": "text"})
    await bot._handle_frame({"type": "channel.updated", "channel": voice_dto("c")})
    await bot._handle_frame(help_frame("c"))
    await drain(bot)
    assert [m["channel_id"] for m in client.sent] == ["c"]


async def test_overwrite_change_refreshes_so_a_newly_visible_channel_is_heard(monkeypatch):
    bot, client, listed = await make_bot(monkeypatch)
    listed.append(voice_dto("hidden"))
    await bot._handle_frame({"type": "overwrite.changed", "channel_id": "hidden"})
    await bot._handle_frame(help_frame("hidden"))
    await drain(bot)
    assert [m["channel_id"] for m in client.sent] == ["hidden"]


async def test_role_change_for_another_member_does_not_refresh(monkeypatch):
    bot, client, _ = await make_bot(monkeypatch)
    await bot._handle_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r"})
    assert channel_fetches(client) == []
    await bot._handle_frame({"type": "member.role_changed", "user_id": "bot-1", "role_id": "r"})
    assert len(channel_fetches(client)) == 1


async def test_miss_backstop_finds_a_channel_the_frames_never_announced(monkeypatch):
    bot, client, listed = await make_bot(monkeypatch)
    listed.append(voice_dto("late"))
    await bot._handle_frame(help_frame("late"))
    await drain(bot)
    assert [m["channel_id"] for m in client.sent] == ["late"]


async def test_miss_backstop_does_not_refetch_within_the_rate_window(monkeypatch):
    bot, client, _ = await make_bot(monkeypatch)
    now = [1000.0]
    bot._clock = lambda: now[0]
    for _ in range(5):
        await bot._handle_frame(help_frame("ghost"))
    assert len(channel_fetches(client)) == 1
    await bot._handle_frame(help_frame("other-ghost"))
    assert len(channel_fetches(client)) == 2
    now[0] += CHANNEL_MISS_REFRESH_SECONDS + 1
    await bot._handle_frame(help_frame("ghost"))
    assert len(channel_fetches(client)) == 3


async def test_a_dropped_frame_is_logged_at_debug(monkeypatch, caplog):
    bot, _, _ = await make_bot(monkeypatch)
    with caplog.at_level(logging.DEBUG, logger="slimbots.bot"):
        await bot._handle_frame(help_frame("ghost"))
    assert any("ghost" in r.getMessage() and r.levelno == logging.DEBUG for r in caplog.records)
