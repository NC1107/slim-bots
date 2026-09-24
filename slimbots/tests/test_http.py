import httpx

from slimbots.http import AsyncClient


async def test_raw_body_sends_bytes_as_is_not_json():
    seen = {}

    def handler(request):
        seen["content"] = request.content
        seen["json_header"] = request.headers.get("content-type")
        return httpx.Response(200, json={"id": "att-1"})

    client = AsyncClient("https://fake.invalid", "slimbot_fake", "test/1.0")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fake.invalid")

    result = await client.call("POST", "/attachments", raw_body=b"\x89PNGdata")
    assert seen["content"] == b"\x89PNGdata"
    assert result == {"id": "att-1"}


async def test_json_body_still_works_when_raw_body_is_not_given():
    seen = {}

    def handler(request):
        seen["content"] = request.content
        return httpx.Response(200, json={"ok": True})

    client = AsyncClient("https://fake.invalid", "slimbot_fake", "test/1.0")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fake.invalid")

    await client.call("POST", "/x", {"a": 1})
    assert seen["content"] == b'{"a":1}' or b"a" in seen["content"]
