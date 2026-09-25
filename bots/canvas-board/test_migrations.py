#!/usr/bin/env python3
"""A 0.2.0 database carried over into 0.3.0 must pick up new columns, not crash; run directly: python3 test_migrations.py."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as board  # noqa: E402


def a_0_2_0_items_table():
    """`items` before `added_by` existed."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            slot INTEGER NOT NULL,
            text TEXT NOT NULL,
            seq INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.execute("INSERT INTO items (id, slot, text, seq, active) VALUES ('o1', 0, 'buy milk', 5, 1)")
    conn.commit()
    return conn


def test_added_by_is_added_to_a_carried_over_table():
    conn = a_0_2_0_items_table()
    board.init_db(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    assert "added_by" in columns


def test_a_row_from_before_the_migration_survives_with_no_credited_author():
    conn = a_0_2_0_items_table()
    board.init_db(conn)
    row = conn.execute("SELECT id, slot, text, seq, active, added_by FROM items WHERE id = 'o1'").fetchone()
    assert row == ("o1", 0, "buy milk", 5, 1, None)


def test_running_init_db_twice_on_an_already_migrated_table_is_a_no_op():
    conn = a_0_2_0_items_table()
    board.init_db(conn)
    board.init_db(conn)
    row = conn.execute("SELECT id, added_by FROM items WHERE id = 'o1'").fetchone()
    assert row == ("o1", None)


def test_a_fresh_0_3_0_database_still_gets_added_by_from_the_create_table_itself():
    conn = sqlite3.connect(":memory:")
    board.init_db(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    assert "added_by" in columns


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
