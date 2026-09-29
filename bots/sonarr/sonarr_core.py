"""bot-sonarr's settings, Sonarr API client, history dedupe and post rendering; see docs/framework.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" there for why."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from slimbots.limits import Cooldown

USER_AGENT = "slimm-bot-sonarr/1.0"
NAMESPACE = uuid.UUID("736f6e61-7272-4000-8000-626f74000000")
HISTORY_PAGE_SIZE = 100
MAX_HISTORY_PAGES = 5
GRAB_WINDOW_SECONDS = 24 * 3600
COMMAND_COOLDOWN_SECONDS = 10
MAX_QUERY_LENGTH = 100
MAX_RESULTS = 8
QUEUE_LIMIT = 10
CALENDAR_DEFAULT_DAYS = 7
CALENDAR_MAX_DAYS = 30
FAILURE_TEXT_MAX = 200

HELP_TEXT = (
    "commands: `!sonarr search <show>`, `!sonarr add <show>` (pick from buttons), `!sonarr queue`, "
    f"`!sonarr calendar [days]` (default {CALENDAR_DEFAULT_DAYS}, max {CALENDAR_MAX_DAYS})."
)

SONARR_URL = ""
SONARR_API_KEY = ""
SONARR_POLL_SECONDS = 60
SONARR_QUALITY_PROFILE = ""
SONARR_ROOT_FOLDER = ""

_command_cooldown = Cooldown(COMMAND_COOLDOWN_SECONDS)


class SonarrAuthError(Exception):
    """The Sonarr API key was rejected. Not something to retry."""


def configure(bot):
    """Resolves every SONARR_* setting through `bot.setting()`; called once, right after `Bot()` is built."""
    global SONARR_URL, SONARR_API_KEY, SONARR_POLL_SECONDS, SONARR_QUALITY_PROFILE, SONARR_ROOT_FOLDER
    SONARR_URL = (bot.setting("SONARR_URL", required=True) or "").rstrip("/")
    SONARR_API_KEY = bot.setting("SONARR_API_KEY", required=True) or ""
    SONARR_POLL_SECONDS = bot.setting("SONARR_POLL_SECONDS", 60, type=int)
    SONARR_QUALITY_PROFILE = bot.setting("SONARR_QUALITY_PROFILE", "") or ""
    SONARR_ROOT_FOLDER = bot.setting("SONARR_ROOT_FOLDER", "") or ""


def check_sonarr_config():
    """Plaintext is fine on loopback or to a bare docker service name, which never leaves the host's bridge."""
    if not SONARR_URL:
        return None
    host = urllib.parse.urlsplit(SONARR_URL).hostname or ""
    private = host in ("localhost", "127.0.0.1", "::1") or "." not in host
    if not SONARR_URL.startswith("https://") and not private:
        return "refusing a plaintext SONARR_URL to a public host: the api key on the wire is a leak"
    return None


def api(method, path, params=None, body=None):
    """One authenticated Sonarr call returning parsed JSON. Sync (urllib); called via asyncio.to_thread."""
    query = urllib.parse.urlencode(params or {}, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{SONARR_URL}/api/v3{path}?{query}", data=data, method=method)
    request.add_header("x-api-key", SONARR_API_KEY)
    request.add_header("user-agent", USER_AGENT)
    if data is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise SonarrAuthError("sonarr api key rejected") from err
        raise


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS announced (key TEXT PRIMARY KEY, detail TEXT NOT NULL, at INTEGER NOT NULL);
        """
    )
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
    now = int(time.time())
    conn.executemany(
        "INSERT INTO announced (key, detail, at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET detail = excluded.detail, at = excluded.at",
        [(key, detail, now) for key, detail in marks],
    )
    conn.commit()


def fetch_history_since(cursor):
    """History records newer than `cursor`, oldest first, paging back only while a page is all new."""
    fresh = []
    for page in range(1, MAX_HISTORY_PAGES + 1):
        params = {
            "page": page, "pageSize": HISTORY_PAGE_SIZE, "sortKey": "date", "sortDirection": "descending",
            "includeSeries": "true", "includeEpisode": "true",
        }
        records = (api("GET", "/history", params) or {}).get("records", [])
        newer = [r for r in records if r["id"] > cursor]
        fresh.extend(newer)
        if len(newer) < HISTORY_PAGE_SIZE or len(records) < HISTORY_PAGE_SIZE:
            break
    return sorted(fresh, key=lambda r: r["id"])


def bootstrap_cursor(conn):
    """A cold start begins at the newest record, so the existing backlog is never announced."""
    if get_cursor(conn) is not None:
        return
    records = (api("GET", "/history", {"page": 1, "pageSize": 1, "sortKey": "date", "sortDirection": "descending"}) or {}).get("records", [])
    advance_cursor(conn, records[0]["id"] if records else 0)


def episode_label(season, number):
    return f"S{int(season):02d}E{int(number):02d}"


def _event_from(record):
    series, episode = record.get("series") or {}, record.get("episode") or {}
    if not series or not episode:
        return None
    return {
        "series_id": record["seriesId"], "series": series.get("title") or "Unknown series", "year": series.get("year"),
        "season": episode.get("seasonNumber", 0), "episode": episode.get("episodeNumber", 0),
        "title": episode.get("title") or "", "quality": ((record.get("quality") or {}).get("quality") or {}).get("name") or "",
        "indexer": (record.get("data") or {}).get("indexer") or "", "message": (record.get("data") or {}).get("message") or "",
        "source": record.get("sourceTitle") or "", "kind": record["eventType"], "id": record["id"],
    }


def _upgraded_episodes(records):
    """Episodes whose old file Sonarr deleted as an upgrade in this batch: their next import is an upgrade."""
    return {
        (r["seriesId"], (r.get("episode") or {}).get("seasonNumber"), (r.get("episode") or {}).get("episodeNumber"))
        for r in records if r["eventType"] == "episodeFileDeleted" and (r.get("data") or {}).get("reason") == "Upgrade"
    }


def plan_events(conn, records):
    """Turns raw history into announcements, dropping repeats; nothing is recorded until a post lands."""
    upgraded = _upgraded_episodes(records)
    seen_details = {}
    now = int(time.time())
    events = []
    for record in records:
        event = _event_from(record) if record["eventType"] in ("grabbed", "downloadFolderImported", "downloadFailed") else None
        if event is None:
            continue
        ident = (event["series_id"], event["season"], event["episode"])
        base = f"{event['series_id']}|{event['season']}|{event['episode']}"
        if event["kind"] == "grabbed":
            key, prior = f"g|{base}", seen_details.get(f"g|{base}") or get_announced(conn, f"g|{base}")
            if prior and now - prior[1] < GRAB_WINDOW_SECONDS:
                continue
            event.update(post="grabbed", key=key, detail=event["quality"])
        elif event["kind"] == "downloadFolderImported":
            key, prior = f"i|{base}", seen_details.get(f"i|{base}") or get_announced(conn, f"i|{base}")
            if prior and prior[0] == event["quality"]:
                continue
            upgrade = prior is not None or ident in upgraded
            event.update(post="upgraded" if upgrade else "downloaded", key=key, detail=event["quality"])
        else:
            key = f"f|{base}|{event['source']}"
            if key in seen_details or get_announced(conn, key):
                continue
            event.update(post="failed", key=key, detail=event["message"])
        seen_details[event["key"]] = (event["detail"], now)
        events.append(event)
    return events


def _episode_range(numbers):
    numbers = sorted(set(numbers))
    if numbers == list(range(numbers[0], numbers[-1] + 1)) and len(numbers) > 1:
        return f"E{numbers[0]:02d}-E{numbers[-1]:02d}"
    return ", ".join(f"E{n:02d}" for n in numbers)


def build_posts(events):
    """One post per (kind, series, season): a season pack is one line, not thirty."""
    groups = {}
    for event in events:
        groups.setdefault((event["post"], event["series_id"], event["season"]), []).append(event)
    posts = []
    for (kind, _series_id, season), group in groups.items():
        first = group[0]
        year = f" ({first['year']})" if first["year"] else ""
        if len(group) == 1:
            what = f"{first['series']}{year} {episode_label(season, first['episode'])}" + (f" - {first['title']}" if first["title"] else "")
        else:
            what = f"{first['series']}{year} S{int(season):02d}, {len(group)} episodes ({_episode_range([e['episode'] for e in group])})"
        posts.append({
            "kind": kind, "what": what, "quality": first["quality"], "indexer": first["indexer"],
            "message": first["message"], "marks": [(e["key"], e["detail"]) for e in group],
            "max_id": max(e["id"] for e in group),
            "message_id": str(uuid.uuid5(NAMESPACE, "|".join(sorted(e["key"] for e in group)) + kind)),
        })
    return sorted(posts, key=lambda p: p["max_id"])


HEADINGS = {"grabbed": "Grabbed", "downloaded": "Downloaded", "upgraded": "Upgraded", "failed": "Failed"}
COLORS = {"grabbed": 0x3B82F6, "downloaded": 0x22C55E, "upgraded": 0x14B8A6, "failed": 0xEF4444}


def render_text(post):
    parts = [f"{HEADINGS[post['kind']]}: {post['what']}"]
    if post["quality"] and post["kind"] != "failed":
        parts.append(f"[{post['quality']}]")
    if post["kind"] == "grabbed" and post["indexer"]:
        parts.append(f"from {post['indexer']}")
    if post["kind"] == "failed" and post["message"]:
        parts.append(f"- {post['message'][:FAILURE_TEXT_MAX]}")
    return " ".join(parts)


def calendar_window(days):
    start = datetime.now(timezone.utc)
    fmt = "%Y-%m-%d"
    return start.strftime(fmt), (start + timedelta(days=days)).strftime(fmt)


def search_series(term):
    return api("GET", "/series/lookup", {"term": term}) or []


def resolve_defaults():
    """The quality profile id and root folder to add with: the configured ones, else the first Sonarr lists."""
    profiles = api("GET", "/qualityprofile") or []
    folders = api("GET", "/rootfolder") or []
    wanted = SONARR_QUALITY_PROFILE.lower()
    profile = next((p for p in profiles if wanted and wanted in (str(p["id"]), p["name"].lower())), None) or (profiles[0] if profiles else None)
    folder = SONARR_ROOT_FOLDER or (folders[0]["path"] if folders else None)
    if profile is None or folder is None:
        raise RuntimeError("sonarr has no quality profile or root folder to add into")
    return profile["id"], folder


def add_series(lookup):
    """Adds a `lookup` result monitored, searching for its missing episodes right away."""
    profile_id, folder = resolve_defaults()
    body = {
        "title": lookup["title"], "tvdbId": lookup["tvdbId"], "titleSlug": lookup.get("titleSlug"),
        "images": lookup.get("images") or [], "seasons": lookup.get("seasons") or [], "year": lookup.get("year"),
        "qualityProfileId": profile_id, "rootFolderPath": folder, "monitored": True, "seasonFolder": True,
        "addOptions": {"monitor": "all", "searchForMissingEpisodes": True},
    }
    return api("POST", "/series", body=body)
