import httpx

from slimbots import Message
from slimbots.http import AsyncClient
from slimbots.testing import FakeAsyncClient


async def test_send_returns_a_message_with_bound_actions():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/messages", {"id": "m1", "content": "hi", "author_id": "bot-1"})
    message = await client.send("c1", "hi")
    assert isinstance(message, Message)
    assert message.id == "m1"
    assert message.channel_id == "c1"
    assert message.content == "hi"


async def test_message_edit_updates_its_own_content_and_calls_the_right_route():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/messages", {"id": "m1", "content": "old"})
    client.respond("PATCH", "/channels/c1/messages/m1", {"id": "m1", "content": "new"})
    message = await client.send("c1", "old")
    result = await message.edit("new")
    assert result is message
    assert message.content == "new"
    assert ("PATCH", "/channels/c1/messages/m1", {"content": "new"}, None) in [
        (m, p, b, params) for m, p, b, params in client.calls
    ]


async def test_message_delete_calls_the_right_route():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/messages", {"id": "m1"})
    client.respond("DELETE", "/channels/c1/messages/m1", None)
    message = await client.send("c1", "hi")
    await message.delete()
    assert ("DELETE", "/channels/c1/messages/m1") in [(m, p) for m, p, _, _ in client.calls]


async def test_message_react_and_remove_reaction_url_encode_the_emoji():
    seen = []

    def handler(request):
        seen.append(request.url.raw_path.decode())
        return httpx.Response(204)

    client = AsyncClient("https://fake.invalid", "slimbot_fake", "test/1.0")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fake.invalid")
    await client.add_reaction("m1", "\U0001f44d")
    await client.remove_reaction("m1", "\U0001f44d")
    assert seen[0].endswith("%F0%9F%91%8D")
    assert seen[1] == seen[0]


async def test_message_pin_and_unpin_call_the_right_routes():
    client = FakeAsyncClient()
    client.respond("PUT", "/channels/c1/messages/m1/pin", None)
    client.respond("DELETE", "/channels/c1/messages/m1/pin", None)
    message = Message({"id": "m1"}, client=client, channel_id="c1")
    await message.pin()
    await message.unpin()
    calls = [(m, p) for m, p, _, _ in client.calls]
    assert ("PUT", "/channels/c1/messages/m1/pin") in calls
    assert ("DELETE", "/channels/c1/messages/m1/pin") in calls


async def test_message_vote_posts_the_option():
    client = FakeAsyncClient()
    client.respond("PUT", "/messages/m1/polls/vote", None)
    message = Message({"id": "m1"}, client=client, channel_id="c1")
    await message.vote(2)
    assert ("PUT", "/messages/m1/polls/vote", {"option": 2}, None) in client.calls


async def test_message_open_thread_returns_a_channel():
    from slimbots import Channel

    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/messages/m1/thread", {"id": "t1", "name": "thread"})
    message = Message({"id": "m1"}, client=client, channel_id="c1")
    channel = await message.open_thread()
    assert isinstance(channel, Channel)
    assert channel.id == "t1"


async def test_upload_attachment_returns_something_send_accepts():
    client = FakeAsyncClient()
    client.respond("POST", "/attachments?filename=pic.png", {"id": "att-1", "content_type": "image/png", "size": 3})
    attachment = await client.upload_attachment(b"\x89\x50\x4e", filename="pic.png")
    assert attachment.id == "att-1"
    client.respond("POST", "/channels/c1/messages", {"id": "m2", "content": "pic"})
    message = await client.send("c1", "pic", attachment_ids=[attachment.id])
    assert message.id == "m2"


async def test_dm_helpers_wrap_the_right_routes():
    client = FakeAsyncClient()
    client.respond("GET", "/dms", [{"channel_id": "dm1", "user": {"id": "u2"}, "unread": 3, "created_at": 1}])
    conversations = await client.list_dms()
    assert conversations[0].channel_id == "dm1"
    assert conversations[0].unread == 3

    client.respond("POST", "/dms/u2", {"channel_id": "dm1", "user": {"id": "u2"}, "unread": 0, "created_at": 1})
    opened = await client.open_dm("u2")
    assert opened.channel_id == "dm1"

    client.respond("DELETE", "/dms/u2", None)
    await client.close_dm("u2")
    assert ("DELETE", "/dms/u2") in [(m, p) for m, p, _, _ in client.calls]
