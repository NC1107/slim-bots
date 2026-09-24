"""Async equivalents of cursor.bootstrap/sync, for a Bot's AsyncClient; see docs/framework.md."""

from . import cursor


async def bootstrap(client, conn, channel_id, table="cursors"):
    if cursor.get(conn, channel_id, table) is not None:
        return
    latest = await client.call("GET", f"/channels/{channel_id}/messages?limit=1")
    cursor.set(conn, channel_id, latest[0]["seq"] if latest else 0, table)


async def sync(client, scopes):
    response = await client.call("POST", "/sync", {"scopes": scopes})
    return response["scopes"]
