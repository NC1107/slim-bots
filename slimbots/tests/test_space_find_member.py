"""`Space.find_member`: cache first, then one fetch, and None instead of an exception when the user is gone."""

from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

USER = {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}


async def test_a_cached_member_is_returned_without_a_request():
    client = FakeAsyncClient()
    space = Space(client)
    client.respond("GET", "/users/u1", USER)
    cached = await space.fetch_member("u1")
    client.calls.clear()
    assert await space.find_member("u1") is cached
    assert client.calls == []


async def test_an_uncached_member_is_fetched_and_cached():
    client = FakeAsyncClient()
    space = Space(client)
    client.respond("GET", "/users/u1", USER)
    member = await space.find_member("u1")
    assert member is not None and member.username == "nick"
    assert space.members["u1"] is member


async def test_a_missing_user_reads_as_none():
    client = FakeAsyncClient()
    space = Space(client)
    client.respond("GET", "/users/gone", ApiError(404, "not found"))
    assert await space.find_member("gone") is None
