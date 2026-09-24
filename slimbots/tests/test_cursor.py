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
