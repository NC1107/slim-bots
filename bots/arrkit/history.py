"""Sonarr and Radarr history: fetch what is new, and decide what is worth announcing exactly once."""

import time
import uuid

from . import store

PAGE_SIZE = 100
MAX_PAGES = 5
GRAB_WINDOW_SECONDS = 24 * 3600
FAILURE_TEXT_MAX = 200
HEADINGS = {"grabbed": "Grabbed", "downloaded": "Downloaded", "upgraded": "Upgraded", "failed": "Failed"}
COLORS = {"grabbed": 0x3B82F6, "downloaded": 0x22C55E, "upgraded": 0x14B8A6, "failed": 0xEF4444}
WATCHED = ("grabbed", "downloadFolderImported", "downloadFailed")


def fetch_since(api, cursor, include):
    """History records newer than `cursor`, oldest first, paging back only while a page is all new."""
    fresh = []
    for page in range(1, MAX_PAGES + 1):
        params = {"page": page, "pageSize": PAGE_SIZE, "sortKey": "date", "sortDirection": "descending", **include}
        records = (api("GET", "/history", params) or {}).get("records", [])
        newer = [r for r in records if r["id"] > cursor]
        fresh.extend(newer)
        if len(newer) < PAGE_SIZE or len(records) < PAGE_SIZE:
            break
    return sorted(fresh, key=lambda r: r["id"])


def bootstrap(conn, api):
    """A cold start begins at the newest record, so the existing backlog is never announced."""
    if store.get_cursor(conn) is not None:
        return
    params = {"page": 1, "pageSize": 1, "sortKey": "date", "sortDirection": "descending"}
    records = (api("GET", "/history", params) or {}).get("records", [])
    store.advance_cursor(conn, records[0]["id"] if records else 0)


def quality_of(record):
    return ((record.get("quality") or {}).get("quality") or {}).get("name") or ""


def plan_events(conn, records, event_from, upgraded):
    """Announcements worth making, repeats dropped; `event_from` gives each a file-independent `ident`, see README.md."""
    seen, now, events = {}, int(time.time()), []
    for record in records:
        event = event_from(record) if record["eventType"] in WATCHED else None
        if event is None:
            continue
        kind, ident = record["eventType"], event["ident"]
        if kind == "grabbed":
            key, prior = f"g|{ident}", seen.get(f"g|{ident}") or store.get_announced(conn, f"g|{ident}")
            if prior and now - prior[1] < GRAB_WINDOW_SECONDS:
                continue
            event.update(post="grabbed", key=key, detail=event["quality"])
        elif kind == "downloadFolderImported":
            key, prior = f"i|{ident}", seen.get(f"i|{ident}") or store.get_announced(conn, f"i|{ident}")
            if prior and prior[0] == event["quality"]:
                continue
            event.update(post="upgraded" if prior or ident in upgraded else "downloaded", key=key, detail=event["quality"])
        else:
            key = f"f|{ident}|{event['source']}"
            if key in seen or store.get_announced(conn, key):
                continue
            event.update(post="failed", key=key, detail=event["message"])
        seen[key] = (event["detail"], now)
        events.append(event)
    return events


def upgrade_idents(records, deleted_type, ident_of):
    """Idents whose old file the service deleted as an upgrade: their next import is an upgrade."""
    return {ident_of(r) for r in records if r["eventType"] == deleted_type and (r.get("data") or {}).get("reason") == "Upgrade"}


def make_post(namespace, kind, what, group, **extra):
    """One announcement for a group of events, with a message id that is the same on every retry."""
    keys = sorted(e["key"] for e in group)
    return {
        "kind": kind, "what": what, "marks": [(e["key"], e["detail"]) for e in group], "max_id": max(e["id"] for e in group),
        "message_id": str(uuid.uuid5(namespace, "|".join(keys) + kind)), **extra,
    }


def render_text(post):
    parts = [f"{HEADINGS[post['kind']]}: {post['what']}"]
    if post["quality"] and post["kind"] != "failed":
        parts.append(f"[{post['quality']}]")
    if post["kind"] == "grabbed" and post["indexer"]:
        parts.append(f"from {post['indexer']}")
    if post["kind"] == "failed" and post["message"]:
        parts.append(f"- {post['message'][:FAILURE_TEXT_MAX]}")
    return " ".join(parts)
