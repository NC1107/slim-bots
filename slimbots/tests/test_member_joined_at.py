"""`Member.joined_at` reads the server's account creation time, and is None when the payload has none."""

from slimbots.models import Member
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

USER = {"id": "u1", "username": "nick", "display_name": "Nick", "role_ids": []}


def test_joined_at_is_the_server_created_at_in_milliseconds():
    assert Member({**USER, "created_at": 1_700_000_000_000}).joined_at == 1_700_000_000_000


def test_joined_at_is_none_when_the_payload_has_no_created_at():
    assert Member(USER).joined_at is None


async def test_a_fetched_member_carries_joined_at():
    client = FakeAsyncClient()
    client.respond("GET", "/users/u1", {**USER, "created_at": 42})
    member = await Space(client).find_member("u1")
    assert member is not None and member.joined_at == 42
