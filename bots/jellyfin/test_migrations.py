#!/usr/bin/env python3
"""A 0.2.0 database opens in place and gains `posted_media`; this pins that a new table never costs old state.
Run directly: python3 test_migrations.py."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JELLYFIN_URL", "https://fake-jellyfin.invalid")
os.environ.setdefault("JELLYFIN_API_KEY", "fake-key")

import jellyfin_core  # noqa: E402


def a_0_2_0_database():
    """`state`/`posted_items`, identical in 0.2.0 and 0.3.0."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE posted_items (item_id TEXT PRIMARY KEY, posted_at INTEGER NOT NULL);
        """
    )
    conn.execute("INSERT INTO state (key, value) VALUES ('cursor', '2024-01-01T00:00:00.0000000Z')")
    conn.execute("INSERT INTO posted_items (item_id, posted_at) VALUES ('m1', 1000)")
    conn.commit()
    return conn


def test_init_db_on_a_carried_over_database_does_not_lose_the_cursor():
    conn = a_0_2_0_database()
    jellyfin_core.init_db(conn)
    assert jellyfin_core.get_cursor(conn) == "2024-01-01T00:00:00.0000000Z"


def test_init_db_on_a_carried_over_database_does_not_lose_posted_items():
    conn = a_0_2_0_database()
    jellyfin_core.init_db(conn)
    assert jellyfin_core.already_posted(conn, "m1")


def test_init_db_on_a_carried_over_database_adds_the_media_table_and_keeps_working():
    conn = a_0_2_0_database()
    jellyfin_core.init_db(conn)
    jellyfin_core.mark_posted(conn, ["e1"], items=[{"Type": "Episode", "SeriesId": "s", "ParentIndexNumber": 1, "IndexNumber": 2}])
    assert conn.execute("SELECT media_key FROM posted_media").fetchall() == [("episode:s:1:2",)]
    assert jellyfin_core.already_posted(conn, "m1")


def test_init_db_twice_is_harmless():
    conn = a_0_2_0_database()
    jellyfin_core.init_db(conn)
    jellyfin_core.init_db(conn)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
