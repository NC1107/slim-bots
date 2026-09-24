import os

import pytest

from slimbots import Bot
from slimbots.testing import FakeAsyncClient


async def _start_without_the_run_loop(bot, monkeypatch):
    """Runs `Bot.start()` far enough to resolve config and open the cursor db, without entering `_run_forever`."""

    async def fake_run_forever():
        return 0

    monkeypatch.setattr(bot, "_run_forever", fake_run_forever)
    return await bot.start()


async def test_missing_url_and_token_is_one_clear_error(monkeypatch):
    monkeypatch.delenv("SLIMM_URL", raising=False)
    monkeypatch.delenv("SLIMM_BOT_TOKEN", raising=False)
    bot = Bot()
    with pytest.raises(RuntimeError) as excinfo:
        await bot.start()
    assert "SLIMM_URL" in str(excinfo.value)
    assert "SLIMM_BOT_TOKEN" in str(excinfo.value)


async def test_require_channels_names_the_missing_var(monkeypatch):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    monkeypatch.delenv("SLIMM_CHANNELS", raising=False)
    bot = Bot(require_channels=True)
    with pytest.raises(RuntimeError) as excinfo:
        await bot.start()
    assert "SLIMM_CHANNELS" in str(excinfo.value)


async def test_channels_env_var_populates_channels_when_not_passed_explicitly(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    monkeypatch.setenv("SLIMM_CHANNELS", "c1,c2")
    monkeypatch.setenv("SLIMM_CURSOR_DB", str(tmp_path / "cursor.db"))
    bot = Bot()
    await _start_without_the_run_loop(bot, monkeypatch)
    assert bot.channels == {"c1", "c2"}
    assert bot._cursor_conn is not None


async def test_explicit_channels_are_not_overridden_by_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    monkeypatch.setenv("SLIMM_CHANNELS", "from-env")
    monkeypatch.setenv("SLIMM_CURSOR_DB", str(tmp_path / "cursor.db"))
    bot = Bot(channels={"explicit"})
    await _start_without_the_run_loop(bot, monkeypatch)
    assert bot.channels == {"explicit"}


async def test_bot_channel_property_is_the_lone_configured_channel():
    assert Bot(channels={"only-one"}).channel == "only-one"
    assert Bot(channels={"a", "b"}).channel is None
    assert Bot().channel is None


async def test_cursor_persists_across_a_fresh_bot_instance(monkeypatch, tmp_path):
    """Two separate Bot()s pointed at the same cursor file see the same cursor - proving it survives a restart."""
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    db_path = str(tmp_path / "shared-cursor.db")

    bot1 = Bot(channels={"c1"}, cursor_path=db_path)

    async def write_a_seq_then_stop():
        bot1._note_seq("c1", 42)
        return 0

    monkeypatch.setattr(bot1, "_run_forever", write_a_seq_then_stop)
    await bot1.start()

    from slimbots import cursor

    bot2 = Bot(channels={"c1"}, cursor_path=db_path)
    seen = {}

    async def read_the_seq():
        seen["value"] = cursor.get(bot2._cursor_conn, "c1")
        return 0

    monkeypatch.setattr(bot2, "_run_forever", read_the_seq)
    await bot2.start()
    assert seen["value"] == 42


async def test_catch_up_replays_backlog_and_advances_the_cursor(monkeypatch, tmp_path):
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/channels/c1/messages?limit=1", [])
    client.respond(
        "POST",
        "/sync",
        {"scopes": [{"channel_id": "c1", "messages": [{"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "hi", "seq": 7}], "reset": False}]},
    )
    bot = Bot(channels={"c1"}, cursor_path=str(tmp_path / "cursor.db"))
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    bot.me_id = "bot-1"

    import sqlite3

    from slimbots import cursor

    bot._cursor_conn = sqlite3.connect(str(tmp_path / "cursor.db"), isolation_level=None)
    cursor.init_table(bot._cursor_conn)

    seen = []

    @bot.event
    async def on_message(message):
        seen.append(message["id"])

    await bot._catch_up()
    assert seen == ["m1"]
    assert cursor.get(bot._cursor_conn, "c1") == 7
