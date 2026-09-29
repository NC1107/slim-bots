"""`ctx.reply_ephemeral` and `AsyncClient.send_ephemeral`: a private answer to the message's own author."""

import pytest

from slimbots.bot import Bot
from slimbots.context import Context
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
