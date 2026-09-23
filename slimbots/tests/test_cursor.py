import sqlite3

import pytest

from slimbots import cursor


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    cursor.init_table(connection)
    yield connection
    connection.close()


def test_get_is_none_before_anything_is_set(conn):
    assert cursor.get(conn, "chan-1") is None


def test_set_then_get(conn):
    cursor.set(conn, "chan-1", 10)
    assert cursor.get(conn, "chan-1") == 10


def test_set_never_moves_backwards(conn):
    cursor.set(conn, "chan-1", 10)
    cursor.set(conn, "chan-1", 5)
    assert cursor.get(conn, "chan-1") == 10


def test_set_advances_forward(conn):
    cursor.set(conn, "chan-1", 10)
    cursor.set(conn, "chan-1", 20)
    assert cursor.get(conn, "chan-1") == 20


def test_channels_are_independent(conn):
    cursor.set(conn, "chan-1", 10)
    cursor.set(conn, "chan-2", 3)
    assert cursor.get(conn, "chan-1") == 10
    assert cursor.get(conn, "chan-2") == 3


class FakeClient:
    def __init__(self, latest_seq):
        self.calls = []
        self._latest_seq = latest_seq

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self._latest_seq is None:
            return []
        return [{"seq": self._latest_seq}]


def test_bootstrap_baselines_at_current_head_on_first_run(conn):
    client = FakeClient(latest_seq=42)
    cursor.bootstrap(client, conn, "chan-1")
    assert cursor.get(conn, "chan-1") == 42


def test_bootstrap_on_an_empty_channel_starts_at_zero(conn):
    client = FakeClient(latest_seq=None)
    cursor.bootstrap(client, conn, "chan-1")
    assert cursor.get(conn, "chan-1") == 0


def test_bootstrap_is_a_no_op_once_a_cursor_exists(conn):
    cursor.set(conn, "chan-1", 7)
    client = FakeClient(latest_seq=999)
    cursor.bootstrap(client, conn, "chan-1")
    assert cursor.get(conn, "chan-1") == 7
    assert client.calls == []


def test_sync_posts_scopes_and_returns_the_response_scopes():
    client = FakeClient(latest_seq=None)
    client.call = lambda method, path, body=None: {
        "scopes": [{"channel_id": "chan-1", "messages": [], "reset": False}]
    }
    result = cursor.sync(client, [{"channel_id": "chan-1", "after_seq": 5}])
    assert result == [{"channel_id": "chan-1", "messages": [], "reset": False}]
