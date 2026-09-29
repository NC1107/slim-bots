"""Buttons on a message and `@bot.button`: what a send carries, how a press is routed and answered."""

import asyncio

import pytest

from slimbots import Button, rows
from slimbots.bot import Bot
from slimbots.testing import FakeAsyncClient


def press(custom_id="hit", channel_id="c1"):
    return {
        "type": "interaction.created", "interaction_id": "i1", "channel_id": channel_id, "message_id": "m1",
        "custom_id": custom_id, "user_id": "u1", "user_display_name": "Alice", "created_at": 1,
    }


def make_bot():
    bot = Bot(prefix="!")
    bot.client = FakeAsyncClient()
    return bot


async def deliver(bot, frame):
    await bot._handle_frame(frame)
    await asyncio.gather(*list(bot._background_tasks))


async def test_a_send_carries_rows_of_buttons_in_wire_shape():
    client = FakeAsyncClient()
    layout = rows([Button("Hit", "hit", style="primary"), Button.link("Rules", "https://example.com")])
    await client.send("c1", "your move", components=layout)
    assert client.sent[-1]["components"] == [
        {"buttons": [
            {"label": "Hit", "style": "primary", "custom_id": "hit"},
            {"label": "Rules", "style": "link", "url": "https://example.com"},
        ]},
    ]


def test_the_caps_and_shapes_are_checked_before_the_server_sees_them():
    with pytest.raises(ValueError):
        Button("x", style="link")
    with pytest.raises(ValueError):
        Button("x")
    with pytest.raises(ValueError):
        Button("x", "id", url="https://example.com")
    with pytest.raises(ValueError):
        Button("x" * 81, "id")
    with pytest.raises(ValueError):
        Button("x", "i" * 101)
    with pytest.raises(ValueError):
        rows(*[[Button("x", f"id{i}")] for i in range(6)])
    with pytest.raises(ValueError):
        rows([Button("x", f"id{i}") for i in range(6)])


async def test_a_press_runs_the_handler_for_its_custom_id_only():
    bot = make_bot()
    seen = []

    @bot.button("hit")
    async def hit(interaction):
        seen.append(("hit", interaction.user_id, interaction.user_display_name))

    @bot.button("stand")
    async def stand(interaction):
        seen.append(("stand",))

    await deliver(bot, press("hit"))
    assert seen == [("hit", "u1", "Alice")]


async def test_a_prefix_matches_a_family_of_buttons():
    bot = make_bot()
    seen = []

    @bot.button(prefix="vote:")
    async def vote(interaction):
        seen.append(interaction.custom_id)

    await deliver(bot, press("vote:yes"))
    await deliver(bot, press("other"))
    assert seen == ["vote:yes"]


async def test_a_handler_can_answer_privately_and_that_counts_as_the_answer():
    bot = make_bot()

    @bot.button("hit")
    async def hit(interaction):
        await interaction.reply_ephemeral("you drew a king")

    await deliver(bot, press("hit"))
    assert bot.client.ephemerals == [
        {"channel_id": "c1", "interaction_id": "i1", "content": "you drew a king"},
    ]
    assert bot.client.acks == []


async def test_a_handler_can_replace_the_buttons_naming_the_press():
    bot = make_bot()

    @bot.button("hit")
    async def hit(interaction):
        await interaction.edit_components([[Button("Hit", "hit", disabled=True)]])

    await deliver(bot, press("hit"))
    edit = bot.client.component_edits[-1]
    assert edit["message_id"] == "m1"
    assert edit["interaction_id"] == "i1"
    assert edit["components"][0]["buttons"][0]["disabled"] is True
    assert bot.client.acks == []


async def test_a_handler_that_answers_nothing_is_acked_for_it():
    bot = make_bot()

    @bot.button("hit")
    async def hit(interaction):
        return None

    await deliver(bot, press("hit"))
    assert bot.client.acks == ["i1"]


async def test_a_handler_that_raises_is_left_unanswered_so_the_member_sees_it_fail():
    bot = make_bot()

    @bot.button("hit")
    async def hit(interaction):
        raise RuntimeError("boom")

    await deliver(bot, press("hit"))
    assert bot.client.acks == []


async def test_a_press_nobody_handles_is_not_acked_and_reaches_on_interaction():
    bot = make_bot()
    seen = []

    @bot.event
    async def on_interaction(interaction):
        seen.append(interaction.custom_id)

    await deliver(bot, press("mystery"))
    assert seen == ["mystery"]
    assert bot.client.acks == []


async def test_a_press_in_an_unlistened_channel_is_ignored():
    bot = Bot(prefix="!", channels=["c9"])
    bot.client = FakeAsyncClient()
    seen = []

    @bot.button("hit")
    async def hit(interaction):
        seen.append(1)

    await deliver(bot, press("hit", channel_id="c1"))
    assert seen == []


def test_a_button_needs_exactly_one_of_custom_id_or_prefix():
    bot = make_bot()
    with pytest.raises(ValueError):
        bot.button()(lambda i: None)
    with pytest.raises(ValueError):
        bot.button("a", prefix="b")(lambda i: None)
