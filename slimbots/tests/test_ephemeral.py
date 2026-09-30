"""`ctx.reply_ephemeral` and `AsyncClient.send_ephemeral`: a private answer to the message's own author."""

import pytest

from slimbots.bot import Bot
from slimbots.context import Context
from slimbots.embeds import Embed
from slimbots.http import ApiError
from slimbots.testing import FakeAsyncClient


def make_ctx(client):
    bot = Bot(prefix="!")
    bot.client = client
    author = type("Author", (), {"id": "u1"})()
    return Context(bot=bot, message={"id": "m1", "channel_id": "c1"}, author=author, channel_id="c1")


async def test_send_ephemeral_posts_the_reply_route_with_the_anchor():
    client = FakeAsyncClient()
    await client.send_ephemeral("c1", "m1", "only you")
    assert client.calls[-1][:3] == (
        "POST", "/channels/c1/ephemeral-messages", {"in_reply_to_id": "m1", "content": "only you"},
    )
    assert client.ephemerals == [{"channel_id": "c1", "in_reply_to_id": "m1", "content": "only you"}]
    assert client.sent == []


async def test_reply_ephemeral_answers_the_message_being_handled():
    client = FakeAsyncClient()
    await make_ctx(client).reply_ephemeral("you have 500 chips")
    assert client.ephemerals[-1]["in_reply_to_id"] == "m1"
    assert client.ephemerals[-1]["channel_id"] == "c1"
    assert client.sent == []


async def test_an_old_server_raises_unless_a_public_fallback_is_asked_for():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/ephemeral-messages", ApiError(404, {"error": "not found"}))
    with pytest.raises(ApiError):
        await make_ctx(client).reply_ephemeral("private")
    assert client.sent == []

    await make_ctx(client).reply_ephemeral("refused", public_fallback=True)
    assert client.sent[-1]["content"] == "refused"
    assert client.sent[-1]["reply_to_id"] == "m1"


async def test_a_403_is_never_papered_over_by_the_public_fallback():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/ephemeral-messages", ApiError(403, {"error": "forbidden"}))
    with pytest.raises(ApiError):
        await make_ctx(client).reply_ephemeral("refused", public_fallback=True)
    assert client.sent == []


async def test_a_private_answer_carries_files_and_an_embed():
    client = FakeAsyncClient()
    embed = Embed(title="Balance", description="500 chips")
    await make_ctx(client).reply_ephemeral(embed=embed, attachment_ids=["ab" * 32])
    body = client.ephemerals[-1]
    assert body["content"] == ""
    assert body["attachment_ids"] == ["ab" * 32]
    assert body["embeds"][0]["title"] == "Balance"


async def test_a_plain_private_answer_sends_no_empty_lists():
    client = FakeAsyncClient()
    await client.send_ephemeral("c1", "m1", "only you")
    assert "embeds" not in client.ephemerals[-1]
    assert "attachment_ids" not in client.ephemerals[-1]


async def test_a_press_answer_carries_an_embed_too():
    client = FakeAsyncClient()
    await client.send_ephemeral_to_press("c1", "i1", "", embeds=[{"title": "Done"}])
    assert client.ephemerals[-1]["interaction_id"] == "i1"
    assert client.ephemerals[-1]["embeds"] == [{"title": "Done"}]
