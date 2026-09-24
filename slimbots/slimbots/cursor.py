"""A per-channel `seq` cursor in sqlite; see `catchup` for the async `/sync` calls built on it."""

from __future__ import annotations

import sqlite3


def init_table(conn: sqlite3.Connection, table: str = "cursors") -> None:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {table} "
        "(channel_id TEXT PRIMARY KEY, after_seq INTEGER NOT NULL)"
    )
    conn.commit()


def get(conn: sqlite3.Connection, channel_id: str, table: str = "cursors") -> int | None:
    row = conn.execute(
        f"SELECT after_seq FROM {table} WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    return row[0] if row else None


def set(conn: sqlite3.Connection, channel_id: str, seq: int, table: str = "cursors") -> None:
    """Advances the stored cursor to `seq`, never backwards - a frame handled twice must not move it earlier."""
    conn.execute(
        f"INSERT INTO {table} (channel_id, after_seq) VALUES (?, ?) "
        f"ON CONFLICT(channel_id) DO UPDATE SET after_seq = excluded.after_seq "
        f"WHERE excluded.after_seq > after_seq",
        (channel_id, seq),
    )
    conn.commit()
