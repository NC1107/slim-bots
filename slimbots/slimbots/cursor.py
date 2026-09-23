"""A per-channel `seq` cursor in sqlite, and the `/sync` catch-up call.

This is storage and transport only. What a template does with the messages
`/sync` hands back - which triggers fire, what state they update - stays in
the template, because that is the part that differs bot to bot.

A bot that deliberately keeps its cursor in memory instead (see bot-roles's
README on why a command-driven bot can accept losing that on restart) has no
reason to reach for this module at all; a plain variable is simpler and nothing
here is missed by skipping it.
"""


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
    """Advances the stored cursor to `seq`, never backwards.

    A frame handled twice (once live, once again via `/sync` after a
    reconnect that turned out not to have dropped anything) must not move
    the cursor earlier than where it already was.
    """
    conn.execute(
        f"INSERT INTO {table} (channel_id, after_seq) VALUES (?, ?) "
        f"ON CONFLICT(channel_id) DO UPDATE SET after_seq = excluded.after_seq "
        f"WHERE excluded.after_seq > after_seq",
        (channel_id, seq),
    )
    conn.commit()


def bootstrap(client, conn, channel_id, table="cursors"):
    """A channel this process has never watched starts at its current head,
    not at the beginning of history - otherwise a bot's first minute would
    be spent re-triggering years of old messages."""
    if get(conn, channel_id, table) is not None:
        return
    latest = client.call("GET", f"/channels/{channel_id}/messages?limit=1")
    set(conn, channel_id, latest[0]["seq"] if latest else 0, table)


def sync(client, scopes):
    """POSTs `/sync` for `scopes` (a list of `{"channel_id", "after_seq"}`)
    and returns the response's own `scopes` list, one entry per input scope,
    each carrying `messages` and a `reset` flag.

    `reset: true` means the gap was too large for `/sync` to answer and the
    cursor should be re-baselined (typically via `bootstrap`) rather than
    trusted to resume exactly where it left off - the same tradeoff slim-m's
    own reactions and pins accept.
    """
    return client.call("POST", "/sync", {"scopes": scopes})["scopes"]
