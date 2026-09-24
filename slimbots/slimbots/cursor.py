"""A per-channel `seq` cursor in sqlite; see `catchup` for the async `/sync` calls built on it."""


def init_table(conn, table="cursors"):
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {table} "
        "(channel_id TEXT PRIMARY KEY, after_seq INTEGER NOT NULL)"
    )
    conn.commit()


def get(conn, channel_id, table="cursors"):
    row = conn.execute(
        f"SELECT after_seq FROM {table} WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    return row[0] if row else None


def set(conn, channel_id, seq, table="cursors"):
    """Advances the stored cursor to `seq`, never backwards - a frame handled twice must not move it earlier."""
    conn.execute(
        f"INSERT INTO {table} (channel_id, after_seq) VALUES (?, ?) "
        f"ON CONFLICT(channel_id) DO UPDATE SET after_seq = excluded.after_seq "
        f"WHERE excluded.after_seq > after_seq",
        (channel_id, seq),
    )
    conn.commit()
