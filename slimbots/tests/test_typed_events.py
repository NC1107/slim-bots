import pytest

from slimbots import Bot
from slimbots.testing import FakeAsyncClient


@pytest.fixture
def client():
    return FakeAsyncClient(me_id="bot-1")


@pytest.fixture
async def bot(client):
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    b = Bot(prefix="!", channels={"c1"})
    b.client = client
    b.space = Space(client)
    b.authors = AuthorFilter(client, space=b.space, ignore_bots=True)
    b.me_id = "bot-1"
    await b.space.refresh_members()
    return b


async def test_message_edited_carries_a_typed_payload(bot):
    seen = []

    @bot.event
    async def on_message_edited(event):
        seen.append(event)

    await bot._handle_frame({
        "type": "message.edited", "channel_id": "c1", "seq": 5, "op_seq": 9,
        "message": {"id": "m1", "content": "new text"},
    })
    assert len(seen) == 1
    assert seen[0].channel_id == "c1"
    assert seen[0].seq == 5
    assert seen[0].op_seq == 9
    assert seen[0].message == {"id": "m1", "content": "new text"}


async def test_channel_scoped_typed_event_is_gated_by_channels(bot):
    seen = []

    @bot.event
    async def on_reactions_changed(event):
        seen.append(event)

    await bot._handle_frame({
        "type": "reactions.changed", "channel_id": "other", "message_id": "m1",
        "reactions": [{"emoji": "x", "count": 1}],
    })
    assert seen == []

    await bot._handle_frame({
        "type": "reactions.changed", "channel_id": "c1", "message_id": "m1",
        "reactions": [{"emoji": "x", "count": 1}],
    })
    assert len(seen) == 1
    assert seen[0].message_id == "m1"
    assert seen[0].reactions == [{"emoji": "x", "count": 1}]


async def test_global_typed_event_is_never_channel_gated(bot):
    seen = []

    @bot.event
    async def on_presence_changed(event):
        seen.append(event)

    await bot._handle_frame({"type": "presence.changed", "user_id": "u1", "status": "online"})
    assert len(seen) == 1
    assert seen[0].user_id == "u1"
    assert seen[0].status == "online"


async def test_a_field_less_event_still_dispatches(bot):
    seen = []

    @bot.event
    async def on_category_changed(event):
        seen.append(event)

    await bot._handle_frame({"type": "category.changed"})
    assert len(seen) == 1


async def test_pre_existing_event_still_gets_the_raw_frame_not_a_wrapper(bot):
    seen = []

    @bot.event
    async def on_member_removed(frame):
        seen.append(frame)

    await bot._handle_frame({"type": "member.removed", "user_id": "u1"})
    assert seen == [{"type": "member.removed", "user_id": "u1"}]


async def test_call_ringing_is_global_so_a_dm_channel_never_needs_to_be_in_channels(bot):
    seen = []

    @bot.event
    async def on_call_ringing(event):
        seen.append(event)

    await bot._handle_frame({
        "type": "call.ringing", "channel_id": "dm-1", "ring_id": "r1", "caller_id": "u1",
    })
    assert len(seen) == 1
    assert seen[0].channel_id == "dm-1"
    assert seen[0].ring_id == "r1"
    assert seen[0].caller_id == "u1"


async def test_member_joined_resolves_and_dispatches_a_live_member(bot, client):
    seen = []

    @bot.event
    async def on_member_join(member):
        seen.append(member)

    client.respond("GET", "/users/u-new", {
        "id": "u-new", "username": "newbie", "display_name": "Newbie",
    })
    await bot._handle_frame({"type": "member.joined", "user_id": "u-new"})
    assert len(seen) == 1
    assert seen[0].id == "u-new"
    assert seen[0].display_name == "Newbie"


async def test_member_joined_for_an_unresolvable_id_dispatches_nothing(bot, client):
    from slimbots.http import ApiError

    seen = []

    @bot.event
    async def on_member_join(member):
        seen.append(member)

    client.respond("GET", "/users/ghost", ApiError(404, "not found"))
    await bot._handle_frame({"type": "member.joined", "user_id": "ghost"})
    assert seen == []


async def test_every_documented_frame_kind_has_a_registered_handler_name():
    from slimbots.bot import _CHANNEL_EVENT_FRAMES, _GLOBAL_EVENT_FRAMES

    expected = {
        "presence.changed", "profile.changed", "channel.created", "channel.updated",
        "channel.deleted", "category.changed", "call.ringing", "call.ring_ended", "reports.changed",
        "message.edited", "message.deleted", "reactions.changed", "thread.updated",
        "message.pinned", "message.unpinned", "poll.voted", "typing.started", "typing.stopped",
        "overwrite.changed", "voice.activity", "canvas.objects.restored", "canvas.cursor.moved",
        "canvas.stroke_preview.updated", "canvas.object.moved", "canvas.object.reordered",
        "canvas.media_slot.changed",
    }
    registered = set(_GLOBAL_EVENT_FRAMES) | set(_CHANNEL_EVENT_FRAMES)
    assert expected <= registered
