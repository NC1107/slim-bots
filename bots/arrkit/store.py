"""The sqlite tables every polling bot here keeps: a cursor, and the dedupe keys already announced."""

import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS announced (key TEXT PRIMARY KEY, detail TEXT NOT NULL, at INTEGER NOT NULL);
"""


def init_db(conn, extra_schema=""):
    conn.executescript(SCHEMA + extra_schema)
    conn.commit()


def get_cursor(conn):
    row = conn.execute("SELECT value FROM state WHERE key = 'history_cursor'").fetchone()
    return int(row[0]) if row else None


def advance_cursor(conn, record_id):
    """Only ever moves forward, so a retried batch cannot rewind it."""
    conn.execute(
        "INSERT INTO state (key, value) VALUES ('history_cursor', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE CAST(excluded.value AS INTEGER) > CAST(state.value AS INTEGER)",
        (str(record_id),),
    )
    conn.commit()


def get_announced(conn, key):
    """`(detail, at)` for a dedupe key, or None."""
    row = conn.execute("SELECT detail, at FROM announced WHERE key = ?", (key,)).fetchone()
    return (row[0], row[1]) if row else None


def mark_announced(conn, marks):
    """Records `(key, detail)` pairs; a key seen again keeps its place with the newer detail and time."""
    now = int(time.time())
    conn.executemany(
        "INSERT INTO announced (key, detail, at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET detail = excluded.detail, at = excluded.at",
        [(key, detail, now) for key, detail in marks],
    )
    conn.commit()
