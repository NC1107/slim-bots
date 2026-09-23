import pytest

from slimbots.testing import FakeClient


def test_me_is_stubbed_by_default():
    client = FakeClient(me_id="bot-42")
    assert client.me() == {"id": "bot-42"}


def test_send_is_recorded_and_answered():
    client = FakeClient()
    client.send("chan-1", "pong", reply_to_id="msg-1")

    assert len(client.sent) == 1
    sent = client.sent[0]
    assert sent["channel_id"] == "chan-1"
    assert sent["content"] == "pong"
    assert sent["reply_to_id"] == "msg-1"
    assert sent["id"]
    assert sent["seq"] == 1


def test_send_keeps_a_given_message_id():
    client = FakeClient()
    client.send("chan-1", "pong", message_id="fixed-id")
    assert client.sent[0]["id"] == "fixed-id"


def test_sent_seq_increments_across_channels():
    client = FakeClient()
    client.send("chan-1", "one")
    client.send("chan-2", "two")
    assert [entry["seq"] for entry in client.sent] == [1, 2]


def test_unstubbed_call_raises_immediately():
    client = FakeClient()
    with pytest.raises(KeyError, match="/reports/history"):
        client.call("GET", "/reports/history?limit=1")


def test_respond_stubs_an_arbitrary_route():
    client = FakeClient()
    client.respond("GET", "/users/u1", {"id": "u1", "display_name": "Alice"})
    assert client.call("GET", "/users/u1") == {"id": "u1", "display_name": "Alice"}


def test_respond_accepts_a_callable_for_a_changing_answer():
    client = FakeClient()
    seqs = iter([10, 20])
    client.respond("GET", "/channels/c1/messages?limit=1", lambda: [{"seq": next(seqs)}])
    assert client.call("GET", "/channels/c1/messages?limit=1")[0]["seq"] == 10
    assert client.call("GET", "/channels/c1/messages?limit=1")[0]["seq"] == 20


def test_calls_records_every_call_in_order():
    client = FakeClient()
    client.respond("GET", "/users/u1", {"id": "u1"})
    client.call("GET", "/users/u1")
    client.send("chan-1", "hi")
    assert [(method, path) for method, path, _ in client.calls] == [
        ("GET", "/users/u1"),
        ("POST", "/channels/chan-1/messages"),
    ]
