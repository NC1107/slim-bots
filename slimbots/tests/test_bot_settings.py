import pytest

from slimbots import Bot


def test_setting_returns_the_default_when_unset(monkeypatch):
    monkeypatch.delenv("JELLYFIN_POLL_SECONDS", raising=False)
    bot = Bot()
    assert bot.setting("JELLYFIN_POLL_SECONDS", 300, type=int) == 300


def test_setting_converts_int_and_list(monkeypatch):
    monkeypatch.setenv("JELLYFIN_POLL_SECONDS", "45")
    monkeypatch.setenv("JELLYFIN_ITEM_TYPES", "Movie, Episode, Audio")
    bot = Bot()
    assert bot.setting("JELLYFIN_POLL_SECONDS", type=int) == 45
    assert bot.setting("JELLYFIN_ITEM_TYPES", type=list) == ["Movie", "Episode", "Audio"]


def test_a_missing_required_setting_is_reported_at_start_not_at_call_time(monkeypatch):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    monkeypatch.delenv("JELLYFIN_URL", raising=False)
    bot = Bot()
    value = bot.setting("JELLYFIN_URL", required=True)  # never raises here
    assert value is None
    with pytest.raises(RuntimeError) as excinfo:
        import asyncio

        asyncio.run(bot.start())
    assert "JELLYFIN_URL" in str(excinfo.value)


async def test_multiple_missing_required_settings_are_reported_together(monkeypatch):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    monkeypatch.delenv("JELLYFIN_URL", raising=False)
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    bot = Bot()
    bot.setting("JELLYFIN_URL", required=True)
    bot.setting("JELLYFIN_API_KEY", required=True)
    with pytest.raises(RuntimeError) as excinfo:
        await bot.start()
    assert "JELLYFIN_URL" in str(excinfo.value)
    assert "JELLYFIN_API_KEY" in str(excinfo.value)


def test_data_path_falls_back_to_the_bots_own_default(monkeypatch):
    monkeypatch.delenv("SLIMM_DB_PATH", raising=False)
    bot = Bot(default_data_path="casino.db")
    assert bot.data_path == "casino.db"


def test_data_path_prefers_the_env_override(monkeypatch, tmp_path):
    override = str(tmp_path / "elsewhere.db")
    monkeypatch.setenv("SLIMM_DB_PATH", override)
    bot = Bot(default_data_path="casino.db")
    assert bot.data_path == override


async def test_cursor_shares_data_path_when_no_explicit_cursor_path_is_given(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    monkeypatch.delenv("SLIMM_DB_PATH", raising=False)
    data_path = str(tmp_path / "casino.db")
    bot = Bot(channels={"c1"}, default_data_path=data_path)

    async def fake_run_forever():
        return 0

    monkeypatch.setattr(bot, "_run_forever", fake_run_forever)
    await bot.start()
    assert bot._cursor_conn is not None
