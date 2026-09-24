import json
import urllib.error

import pytest

from slimbots.client import Client, is_not_found, is_token_revoked, socket_url


def test_https_becomes_wss():
    assert socket_url("https://my.space") == "wss://my.space/ws"


@pytest.mark.parametrize(
    "authority", ["localhost:8080", "127.0.0.1:8080", "[::1]:8080"]
)
def test_http_loopback_allowed(authority, capsys):
    url = socket_url(f"http://{authority}")
    assert url == f"ws://{authority}/ws"
    assert "loopback only" in capsys.readouterr().err


def test_http_off_loopback_refused():
    with pytest.raises(RuntimeError, match="https"):
        socket_url("http://example.com")


def test_unknown_scheme_refused():
    with pytest.raises(RuntimeError):
        socket_url("ftp://example.com")


def test_client_requires_base_and_token():
    with pytest.raises(ValueError):
        Client("", "token", "ua")
    with pytest.raises(ValueError):
        Client("https://x", "", "ua")


class FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode() if payload is not None else b""

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_call_sends_auth_and_user_agent(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["headers"] = dict(request.header_items())
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        return FakeResponse({"ok": True})

    monkeypatch.setattr("slimbots.client.urllib.request.urlopen", fake_urlopen)
    client = Client("https://my.space", "slimbot_abc", "slimm-bot-test/1.0")
    result = client.call("GET", "/me")

    assert result == {"ok": True}
    assert seen["headers"]["Authorization"] == "Bearer slimbot_abc"
    assert seen["headers"]["User-agent"] == "slimm-bot-test/1.0"
    assert seen["url"] == "https://my.space/me"
    assert seen["method"] == "GET"


def test_send_keeps_id_fixed_across_a_retry(monkeypatch):
    bodies = []
    attempt = {"n": 0}

    def fake_urlopen(request, timeout):
        attempt["n"] += 1
        bodies.append(json.loads(request.data))
        if attempt["n"] < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        return FakeResponse({"id": bodies[-1]["id"]})

    monkeypatch.setattr("slimbots.client.urllib.request.urlopen", fake_urlopen)
    client = Client("https://my.space", "slimbot_abc", "ua")
    client.send("chan-1", "pong", sleep=lambda _: None)

    assert attempt["n"] == 3
    ids = {b["id"] for b in bodies}
    contents = {b["content"] for b in bodies}
    assert len(ids) == 1
    assert len(contents) == 1


def test_send_never_retries_a_rejected_4xx(monkeypatch):
    attempt = {"n": 0}

    def fake_urlopen(request, timeout):
        attempt["n"] += 1
        raise urllib.error.HTTPError(request.full_url, 422, "bad", {}, None)

    monkeypatch.setattr("slimbots.client.urllib.request.urlopen", fake_urlopen)
    client = Client("https://my.space", "slimbot_abc", "ua")
    with pytest.raises(urllib.error.HTTPError):
        client.send("chan-1", "pong")
    assert attempt["n"] == 1


def test_send_uses_given_message_id():
    # This checks the body assembled, so it stubs call rather than the network.
    client = Client("https://my.space", "slimbot_abc", "ua")
    calls = []
    client.call = lambda method, path, body=None: calls.append(body) or {"ok": True}
    client.send("chan-1", "hi", message_id="fixed-id")
    assert calls[0]["id"] == "fixed-id"


def test_send_includes_attachment_ids_only_when_given():
    client = Client("https://my.space", "slimbot_abc", "ua")
    calls = []
    client.call = lambda method, path, body=None: calls.append(body) or {"ok": True}
    client.send("chan-1", "hi")
    assert "attachment_ids" not in calls[0]
    client.send("chan-1", "hi", attachment_ids=["att-1"])
    assert calls[1]["attachment_ids"] == ["att-1"]


def test_call_sends_raw_body_without_json_encoding(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["data"] = request.data
        seen["headers"] = dict(request.header_items())
        return FakeResponse({"id": "att-1"})

    monkeypatch.setattr("slimbots.client.urllib.request.urlopen", fake_urlopen)
    client = Client("https://my.space", "slimbot_abc", "ua")
    result = client.call(
        "POST",
        "/attachments?filename=poster.jpg",
        raw_body=b"\x89PNG...",
        headers={"content-type": "application/octet-stream"},
    )

    assert result == {"id": "att-1"}
    assert seen["data"] == b"\x89PNG..."
    assert seen["headers"]["Content-type"] == "application/octet-stream"


def test_call_extra_headers_do_not_replace_auth_or_user_agent(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["headers"] = dict(request.header_items())
        return FakeResponse({"ok": True})

    monkeypatch.setattr("slimbots.client.urllib.request.urlopen", fake_urlopen)
    client = Client("https://my.space", "slimbot_abc", "slimm-bot-test/1.0")
    client.call("GET", "/me", headers={"x-extra": "1"})

    assert seen["headers"]["Authorization"] == "Bearer slimbot_abc"
    assert seen["headers"]["User-agent"] == "slimm-bot-test/1.0"
    assert seen["headers"]["X-extra"] == "1"


def test_is_token_revoked():
    assert is_token_revoked(urllib.error.HTTPError("u", 401, "e", {}, None))
    assert not is_token_revoked(urllib.error.HTTPError("u", 403, "e", {}, None))
    assert not is_token_revoked(urllib.error.URLError("boom"))


def test_is_not_found():
    assert is_not_found(urllib.error.HTTPError("u", 404, "e", {}, None))
    assert not is_not_found(urllib.error.HTTPError("u", 401, "e", {}, None))
