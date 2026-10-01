"""The watch-session routes of `AsyncClient` (slim-m decision 0050), against a mock transport."""

import json

import httpx
import pytest

from slimbots.http import ApiError, AsyncClient


def client_seeing(seen, status=204, body=None):
    def handler(request):
        seen.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)

    client = AsyncClient("https://fake.invalid", "slimbot_fake", "test/1.0")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fake.invalid")
    return client


async def test_setting_a_session_puts_the_documented_body():
    seen = []
    client = client_seeing(seen)
    await client.set_watch_session(
        "c1", item_id="i1", title="Film", playing=True, position_ms=1500, duration_ms=9000, seeked=True, controller_user_id="u1",
    )
    assert seen == [("PUT", "/channels/c1/watch-session", {
        "item_id": "i1", "title": "Film", "playing": True, "position_ms": 1500, "duration_ms": 9000,
        "seeked": True, "controller_user_id": "u1",
    })]


async def test_optional_fields_are_left_out_of_the_body_when_unset():
    seen = []
    await client_seeing(seen).set_watch_session("c1", item_id="i1", title="Film", playing=False, position_ms=0)
    assert seen[0][2] == {"item_id": "i1", "title": "Film", "playing": False, "position_ms": 0}


async def test_a_tick_posts_playing_and_position():
    seen = []
    await client_seeing(seen).tick_watch_session("c1", playing=False, position_ms=42)
    assert seen == [("POST", "/channels/c1/watch-session/tick", {"playing": False, "position_ms": 42})]


async def test_ending_deletes_and_reading_gets():
    seen = []
    client = client_seeing(seen, status=200, body={"item_id": "i1"})
    assert await client.get_watch_session("c1") == {"item_id": "i1"}
    await client.end_watch_session("c1")
    assert [(m, p) for m, p, _ in seen] == [("GET", "/channels/c1/watch-session"), ("DELETE", "/channels/c1/watch-session")]


async def test_a_conflict_is_raised_not_retried():
    seen = []
    client = client_seeing(seen, status=409, body={"error": "another bot is playing here"})
    with pytest.raises(ApiError) as caught:
        await client.set_watch_session("c1", item_id="i1", title="Film", playing=True, position_ms=0)
    assert caught.value.status == 409
    assert len(seen) == 1
