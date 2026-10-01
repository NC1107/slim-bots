"""Message menu entries and call controls: what a bot registers and how a use is routed and answered."""

import asyncio

import pytest

from slimbots import Permissions
from slimbots.http import ApiError
from slimbots.bot import Bot
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient
from slimbots.ui import UiEntry, register_ui


def frame(kind, entry_id, **extra):
    base = {
        "type": "interaction.created", "interaction_id": "i1", "channel_id": "c1", "kind": kind,
        "custom_id": entry_id, "user_id": "u1", "user_display_name": "Alice", "created_at": 1,
    }
    if kind == "message_menu":
        base["message_id"] = "m1"
    return {**base, **extra}


def make_bot():
    bot = Bot(prefix="!")
    bot.client = FakeAsyncClient()
    return bot


async def deliver(bot, payload):
    await bot._handle_frame(payload)
    await asyncio.gather(*list(bot._background_tasks))


async def test_the_whole_set_is_registered_in_wire_shape():
    bot = make_bot()

    @bot.message_menu("translate", " Translate ", permission=int(Permissions.MANAGE_MESSAGES))
    async def translate(interaction):
        pass

    @bot.call_control("pause", "Pause", icon="pause")
    async def pause(interaction):
        pass

    assert await register_ui(bot.client, bot._ui) is True
    method, path, body, _ = bot.client.calls[-1]
    assert (method, path) == ("PUT", "/bots/ui")
    assert body == {
        "message_menu": [
            {"id": "translate", "label": "Translate", "permission": int(Permissions.MANAGE_MESSAGES)},
        ],
        "call_controls": [{"id": "pause", "label": "Pause", "icon": "pause"}],
    }


async def test_a_bot_with_no_entries_registers_nothing_on_connect():
    bot = make_bot()
    assert not bot._ui
    bot._ui.add("message_menu", UiEntry("a", "A"), lambda i: None)
    assert bot._ui


def test_the_caps_and_shapes_are_checked_before_the_server_sees_them():
    bot = make_bot()
    for i in range(5):
        bot.message_menu(f"e{i}", "x")(lambda interaction: None)
    with pytest.raises(ValueError):
        bot.message_menu("e5", "x")(lambda interaction: None)
    with pytest.raises(ValueError):
        bot.message_menu("e0", "x")(lambda interaction: None)
    with pytest.raises(ValueError):
        UiEntry("bad id", "x")
    with pytest.raises(ValueError):
        UiEntry("a", "x" * 33)
    with pytest.raises(ValueError):
        UiEntry("a", "x", icon="skull")
    with pytest.raises(ValueError):
        bot._ui.add("message_menu", UiEntry("i", "x", icon="play"), lambda interaction: None)


async def test_a_menu_use_runs_its_handler_with_the_message_and_is_acked_for_it():
    bot = make_bot()
    seen = []

    @bot.message_menu("translate", "Translate")
    async def translate(interaction):
        seen.append((interaction.kind, interaction.message_id, interaction.user_id, interaction.user_display_name))

    await deliver(bot, frame("message_menu", "translate"))
    assert seen == [("message_menu", "m1", "u1", "Alice")]
    assert bot.client.acks == ["i1"]


async def test_a_call_control_has_no_message_and_can_answer_privately():
    bot = make_bot()
    seen = []

    @bot.call_control("pause", "Pause", icon="pause")
    async def pause(interaction):
        seen.append(interaction.message_id)
        await interaction.reply_ephemeral("paused")

    await deliver(bot, frame("call_control", "pause"))
    assert seen == [None]
    assert bot.client.ephemerals == [{"channel_id": "c1", "interaction_id": "i1", "content": "paused"}]
    assert bot.client.acks == []


async def test_the_same_id_on_two_surfaces_routes_by_kind():
    bot = make_bot()
    seen = []

    @bot.message_menu("go", "Go")
    async def menu(interaction):
        seen.append("menu")

    @bot.call_control("go", "Go")
    async def control(interaction):
        seen.append("control")

    await deliver(bot, frame("call_control", "go"))
    assert seen == ["control"]


async def test_a_handler_that_raises_is_left_unanswered_so_the_member_sees_it_fail():
    bot = make_bot()

    @bot.call_control("stop", "Stop")
    async def stop(interaction):
        raise RuntimeError("boom")

    await deliver(bot, frame("call_control", "stop"))
    assert bot.client.acks == []


async def test_an_unknown_entry_reaches_on_interaction_and_is_not_acked():
    bot = make_bot()
    seen = []

    @bot.event
    async def on_interaction(interaction):
        seen.append(interaction.custom_id)

    await deliver(bot, frame("message_menu", "nobody-registered-this"))
    assert seen == ["nobody-registered-this"]
    assert bot.client.acks == []


async def test_a_button_press_without_a_kind_still_reaches_button_handlers():
    bot = make_bot()
    seen = []

    @bot.button("hit")
    async def hit(interaction):
        seen.append(interaction.kind)

    payload = frame("button", "hit", message_id="m1")
    del payload["kind"]
    await deliver(bot, payload)
    assert seen == ["button"]


@pytest.mark.parametrize("hidden", ["​", "‮", "ㅤ", "⠀", "­", "﻿", "\x00", "⁠", "\U000e0041"])
def test_a_label_holding_an_invisible_character_is_refused_before_it_is_sent(hidden):
    with pytest.raises(ValueError, match="invisible"):
        UiEntry("pause", f"Pa{hidden}use")


def test_a_label_made_only_of_invisible_characters_is_refused():
    with pytest.raises(ValueError):
        UiEntry("pause", "ㅤ")


def test_a_variation_selector_stays_legal_because_it_makes_a_heart_an_emoji():
    label = "Like " + chr(0x2764) + chr(0xFE0F)
    assert UiEntry("like", label).label == label


async def test_a_400_from_registration_ends_the_run_instead_of_retrying_forever():
    bot = make_bot()
    bot.space = Space(bot.client)
    bot.base_delay = bot.max_delay = 0.001
    bot.client.respond("PUT", "/bots/ui", ApiError(400, {"error": "an entry label cannot hold control or invisible characters"}))
    bot._ui.add("call_control", UiEntry("pause", "Pause"), lambda i: None)
    attempts = 0
    original = bot._connect_once

    async def counted(reset_delay):
        nonlocal attempts
        attempts += 1
        await original(reset_delay)

    bot._connect_once = counted
    assert await asyncio.wait_for(bot._run_forever(), 2) == 1
    assert attempts == 1


async def test_a_call_control_reaches_a_channel_scoped_bot_without_listening_to_voice_chats(monkeypatch):
    monkeypatch.delenv("SLIMM_LISTEN_VOICE_CHATS", raising=False)
    bot = make_bot()
    bot.channels = {"text"}
    heard = []

    @bot.call_control("pause", "Pause")
    async def pause(interaction):
        heard.append(interaction.channel_id)

    await deliver(bot, frame("call_control", "pause", channel_id="voice-1"))
    assert heard == ["voice-1"]


async def test_a_message_menu_use_in_an_unscoped_channel_is_still_dropped(monkeypatch):
    monkeypatch.delenv("SLIMM_LISTEN_VOICE_CHATS", raising=False)
    bot = make_bot()
    bot.channels = {"text"}
    heard = []

    @bot.message_menu("translate", "Translate")
    async def translate(interaction):
        heard.append(interaction.channel_id)

    await deliver(bot, frame("message_menu", "translate", channel_id="elsewhere"))
    assert heard == []
