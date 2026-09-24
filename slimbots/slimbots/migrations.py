"""Adding a column to an existing table without losing the rows already in it; see docs/framework.md."""

from __future__ import annotations

import sqlite3


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """`columns` is `{name: "TYPE [NOT NULL DEFAULT ...]"}`; adds whatever the table is missing."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    conn.commit()
