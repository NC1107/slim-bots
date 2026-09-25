#!/usr/bin/env python3
"""A 0.2.0 `hands` table has no hand_index/status and a 2-column primary key; run directly: python3 test_migrations.py."""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blackjack  # noqa: E402


def a_0_2_0_hands_table():
    """`hands` before split existed: one row per (channel_id, user_id), no hand_index/status."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE hands (
            channel_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            stake INTEGER NOT NULL,
            player_cards TEXT NOT NULL,
            dealer_cards TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            PRIMARY KEY (channel_id, user_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO hands (channel_id, user_id, stake, player_cards, dealer_cards, started_at) "
        "VALUES ('c1', 'u1', 100, '7H,8H', 'KC,2D', 1000)"
    )
    conn.commit()
    return conn


def test_hand_index_and_status_are_added_to_a_carried_over_table():
    conn = a_0_2_0_hands_table()
    blackjack.init_table(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(hands)")}
    assert {"hand_index", "status"} <= columns


def test_an_in_progress_0_2_0_hand_survives_as_hand_index_zero_and_active():
    conn = a_0_2_0_hands_table()
    blackjack.init_table(conn)
    hand = blackjack.active_hand(conn, "c1", "u1")
    assert hand == (0, 100, ["7H", "8H"], ["KC", "2D"])


def test_the_primary_key_is_actually_widened_not_just_the_columns_added():
    """The real bug: a plain ADD COLUMN leaves the old 2-column primary key in place, so a later
    split (a second row for the same channel_id/user_id) would raise an IntegrityError."""
    conn = a_0_2_0_hands_table()
    blackjack.init_table(conn)
    blackjack.insert_split_hand(conn, "c1", "u1", 1, 100, ["9H", "9D"], ["KC", "2D"])
    rows = blackjack.round_hands(conn, "c1", "u1")
    assert len(rows) == 2


def test_running_init_table_twice_on_an_already_migrated_table_is_a_no_op():
    conn = a_0_2_0_hands_table()
    blackjack.init_table(conn)
    blackjack.init_table(conn)
    hand = blackjack.active_hand(conn, "c1", "u1")
    assert hand == (0, 100, ["7H", "8H"], ["KC", "2D"])


def test_a_fresh_0_3_0_database_gets_the_full_schema_directly():
    conn = sqlite3.connect(":memory:")
    blackjack.init_table(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(hands)")}
    assert {"hand_index", "status"} <= columns


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
