#!/usr/bin/env python3
"""A 0.2.0 `reminders` table has no recurrence columns; run directly: python3 test_migrations.py."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as reminders  # noqa: E402

RECUR_COLUMNS = {"recur_kind", "recur_interval_seconds", "recur_weekday", "recur_hour", "recur_minute", "recur_tz"}


def a_0_2_0_reminders_table():
    """`reminders` before recurrence existed."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE reminders (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            request_message_id TEXT NOT NULL,
            due_at INTEGER NOT NULL,
            text TEXT NOT NULL,
            sent INTEGER NOT NULL DEFAULT 0,
            cancelled INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO reminders (id, channel_id, user_id, request_message_id, due_at, text, created_at) "
        "VALUES ('r1', 'c1', 'u1', 'm1', 1000, 'check the oven', 900)"
    )
    conn.commit()
    return conn


def test_recurrence_columns_are_added_to_a_carried_over_table():
    conn = a_0_2_0_reminders_table()
    reminders.init_db(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reminders)")}
    assert RECUR_COLUMNS <= columns


def test_a_row_from_before_the_migration_survives_as_a_plain_one_off():
    conn = a_0_2_0_reminders_table()
    reminders.init_db(conn)
    row = conn.execute("SELECT id, text, recur_kind FROM reminders WHERE id = 'r1'").fetchone()
    assert row == ("r1", "check the oven", None)


def test_the_new_user_prefs_table_exists_alongside_the_carried_over_one():
    conn = a_0_2_0_reminders_table()
    reminders.init_db(conn)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "user_prefs" in tables


def test_running_init_db_twice_on_an_already_migrated_table_is_a_no_op():
    conn = a_0_2_0_reminders_table()
    reminders.init_db(conn)
    reminders.init_db(conn)
    row = conn.execute("SELECT id, text FROM reminders WHERE id = 'r1'").fetchone()
    assert row == ("r1", "check the oven")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
