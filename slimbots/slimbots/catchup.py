"""Async equivalents of cursor.bootstrap/sync, for a Bot's AsyncClient; see docs/framework.md."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from . import cursor

if TYPE_CHECKING:
    from .http import AsyncClient


async def bootstrap(client: AsyncClient, conn: sqlite3.Connection, channel_id: str, table: str = "cursors") -> None:
    if cursor.get(conn, channel_id, table) is not None:
        return
    latest = await client.call("GET", f"/channels/{channel_id}/messages?limit=1")
    cursor.set(conn, channel_id, latest[0]["seq"] if latest else 0, table)


async def sync(client: AsyncClient, scopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    response = await client.call("POST", "/sync", {"scopes": scopes})
    return response["scopes"]
