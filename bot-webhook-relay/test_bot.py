#!/usr/bin/env python3
"""Unit and light integration tests for bot.py, using slimbots.testing.FakeClient
so the relay's own HTTP surface is exercised with no live deployment.

Run it directly, no test framework needed:

    pip install -r requirements.txt
    python3 test_bot.py
"""

import http.client
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot  # noqa: E402
from slimbots.testing import FakeClient  # noqa: E402


def test_render_content_plain():
    assert bot.render_content({"content": "hello"}) == "hello"


def test_render_content_with_username_is_a_label_not_an_author():
    rendered = bot.render_content({"content": "disk full", "username": "sonarr"})
    assert rendered == "**sonarr:** disk full"


def test_render_content_rejects_empty_or_missing():
    assert bot.render_content({}) is None
    assert bot.render_content({"content": ""}) is None
    assert bot.render_content({"content": "   "}) is None
    assert bot.render_content({"content": 5}) is None


def test_message_id_is_stable_for_the_same_key_and_body():
    body = b'{"content": "x"}'
    first = bot.message_id_for("job-42", body)
    second = bot.message_id_for("job-42", body)
    assert first == second


def test_message_id_changes_with_the_body_even_for_the_same_key():
    a = bot.message_id_for("job-42", b'{"content": "x"}')
    b = bot.message_id_for("job-42", b'{"content": "y"}')
    assert a != b


def test_message_id_is_fresh_every_time_without_a_key():
    body = b'{"content": "x"}'
    first = bot.message_id_for(None, body)
    second = bot.message_id_for(None, body)
    assert first != second


class _RunningRelay:
    """Starts bot.py's own Handler on a random port, with a FakeClient
    standing in for slim-m, and tears it down on exit."""

    def __init__(self, shared_secret=""):
        bot.CHANNEL = "chan-1"
        bot.PATH = "/post"
        bot.SHARED_SECRET = shared_secret
        self.client = FakeClient()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), bot.Handler)
        self.server.client = self.client
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def post(self, body, headers=None, path="/post"):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        conn.request("POST", path, body=data, headers=headers or {})
        response = conn.getresponse()
        status, payload = response.status, response.read()
        conn.close()
        return status, payload

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def test_a_plain_post_is_relayed_and_answered_with_204():
    relay = _RunningRelay()
    try:
        status, payload = relay.post({"content": "backup finished"})
        assert status == 204
        assert payload == b""
        assert relay.client.sent[0]["content"] == "backup finished"
        assert relay.client.sent[0]["channel_id"] == "chan-1"
    finally:
        relay.close()


def test_bad_json_is_a_400_and_nothing_is_sent():
    relay = _RunningRelay()
    try:
        status, payload = relay.post(b"not json")
        assert status == 400
        assert relay.client.sent == []
    finally:
        relay.close()


def test_missing_content_is_a_400():
    relay = _RunningRelay()
    try:
        status, _ = relay.post({"username": "sonarr"})
        assert status == 400
        assert relay.client.sent == []
    finally:
        relay.close()


def test_wrong_path_is_a_404():
    relay = _RunningRelay()
    try:
        status, _ = relay.post({"content": "hi"}, path="/somewhere-else")
        assert status == 404
    finally:
        relay.close()


def test_shared_secret_required_when_configured():
    relay = _RunningRelay(shared_secret="topsecret")
    try:
        status, _ = relay.post({"content": "hi"})
        assert status == 401
        assert relay.client.sent == []

        status, _ = relay.post({"content": "hi"}, headers={"X-Webhook-Secret": "wrong"})
        assert status == 401

        status, _ = relay.post({"content": "hi"}, headers={"X-Webhook-Secret": "topsecret"})
        assert status == 204
        assert len(relay.client.sent) == 1
    finally:
        relay.close()


def test_a_retried_request_with_the_same_idempotency_key_sends_once():
    relay = _RunningRelay()
    try:
        headers = {"Idempotency-Key": "job-42"}
        first_status, _ = relay.post({"content": "disk at 95%"}, headers=headers)
        second_status, _ = relay.post({"content": "disk at 95%"}, headers=headers)

        assert first_status == 204
        assert second_status == 204
        # FakeClient answers every send, but the underlying id is the same both times.
        assert relay.client.sent[0]["id"] == relay.client.sent[1]["id"]
    finally:
        relay.close()


def test_requests_without_a_key_are_never_deduplicated():
    relay = _RunningRelay()
    try:
        relay.post({"content": "heartbeat"})
        relay.post({"content": "heartbeat"})
        ids = {entry["id"] for entry in relay.client.sent}
        assert len(ids) == 2, "two undistinguished posts must both go through"
    finally:
        relay.close()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
