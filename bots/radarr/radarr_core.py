"""bot-radarr's settings, Radarr API client, history dedupe and post rendering; see docs/framework.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" there for why."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from slimbots.limits import Cooldown

USER_AGENT = "slimm-bot-radarr/1.0"
NAMESPACE = uuid.UUID("72616461-7272-4000-8000-626f74000000")
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
BATCH_THRESHOLD = 5
BATCH_NAMES = 5

HELP_TEXT = (
    "commands: `!radarr search <movie>`, `!radarr add <movie>` (pick from buttons), `!radarr queue`, "
    f"`!radarr calendar [days]` (upcoming releases, default {CALENDAR_DEFAULT_DAYS}, max {CALENDAR_MAX_DAYS})."
)

RADARR_URL = ""
RADARR_API_KEY = ""
RADARR_POLL_SECONDS = 60
RADARR_QUALITY_PROFILE = ""
RADARR_ROOT_FOLDER = ""

_command_cooldown = Cooldown(COMMAND_COOLDOWN_SECONDS)


class RadarrAuthError(Exception):
    """The Radarr API key was rejected. Not something to retry."""


def configure(bot):
    """Resolves every RADARR_* setting through `bot.setting()`; called once, right after `Bot()` is built."""
    global RADARR_URL, RADARR_API_KEY, RADARR_POLL_SECONDS, RADARR_QUALITY_PROFILE, RADARR_ROOT_FOLDER
    RADARR_URL = (bot.setting("RADARR_URL", required=True) or "").rstrip("/")
    RADARR_API_KEY = bot.setting("RADARR_API_KEY", required=True) or ""
    RADARR_POLL_SECONDS = bot.setting("RADARR_POLL_SECONDS", 60, type=int)
    RADARR_QUALITY_PROFILE = bot.setting("RADARR_QUALITY_PROFILE", "") or ""
    RADARR_ROOT_FOLDER = bot.setting("RADARR_ROOT_FOLDER", "") or ""


def check_radarr_config():
    """Plaintext is fine on loopback or to a bare docker service name, which never leaves the host's bridge."""
    if not RADARR_URL:
        return None
    host = urllib.parse.urlsplit(RADARR_URL).hostname or ""
    private = host in ("localhost", "127.0.0.1", "::1") or "." not in host
    if not RADARR_URL.startswith("https://") and not private:
        return "refusing a plaintext RADARR_URL to a public host: the api key on the wire is a leak"
    return None


def api(method, path, params=None, body=None):
    """One authenticated Radarr call returning parsed JSON. Sync (urllib); called via asyncio.to_thread."""
    query = urllib.parse.urlencode(params or {}, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{RADARR_URL}/api/v3{path}?{query}", data=data, method=method)
    request.add_header("x-api-key", RADARR_API_KEY)
    request.add_header("user-agent", USER_AGENT)
    if data is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise RadarrAuthError("radarr api key rejected") from err
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
            "includeMovie": "true",
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


def movie_key(record):
    """The provider id a movie is deduped on, so a replaced file or a re-added entry keeps the same identity."""
    movie = record.get("movie") or {}
    if movie.get("tmdbId"):
        return f"tmdb:{movie['tmdbId']}"
    return f"imdb:{movie['imdbId']}" if movie.get("imdbId") else f"radarr:{record.get('movieId')}"


def _event_from(record):
    movie = record.get("movie") or {}
    if not movie:
        return None
    data = record.get("data") or {}
    return {
        "movie": movie_key(record), "title": movie.get("title") or "Unknown movie", "year": movie.get("year"),
        "quality": ((record.get("quality") or {}).get("quality") or {}).get("name") or "",
        "indexer": data.get("indexer") or "", "message": data.get("message") or "",
        "source": record.get("sourceTitle") or "", "kind": record["eventType"], "id": record["id"],
    }


def _upgraded_movies(records):
    """Movies whose old file Radarr deleted as an upgrade in this batch: their next import is an upgrade."""
    return {
        movie_key(r) for r in records
        if r["eventType"] == "movieFileDeleted" and (r.get("data") or {}).get("reason") == "Upgrade"
    }


def plan_events(conn, records):
    """Turns raw history into announcements, dropping repeats; nothing is recorded until a post lands."""
    upgraded = _upgraded_movies(records)
    seen = {}
    now = int(time.time())
    events = []
    for record in records:
        event = _event_from(record) if record["eventType"] in ("grabbed", "downloadFolderImported", "downloadFailed") else None
        if event is None:
            continue
        if event["kind"] == "grabbed":
            key = f"g|{event['movie']}"
            prior = seen.get(key) or get_announced(conn, key)
            if prior and now - prior[1] < GRAB_WINDOW_SECONDS:
                continue
            event.update(post="grabbed", key=key, detail=event["quality"])
        elif event["kind"] == "downloadFolderImported":
            key = f"i|{event['movie']}"
            prior = seen.get(key) or get_announced(conn, key)
            if prior and prior[0] == event["quality"]:
                continue
            upgrade = prior is not None or event["movie"] in upgraded
            event.update(post="upgraded" if upgrade else "downloaded", key=key, detail=event["quality"])
        else:
            key = f"f|{event['movie']}|{event['source']}"
            if key in seen or get_announced(conn, key):
                continue
            event.update(post="failed", key=key, detail=event["message"])
        seen[event["key"]] = (event["detail"], now)
        events.append(event)
    return events


def movie_name(event):
    return f"{event['title']} ({event['year']})" if event["year"] else event["title"]


def burst_summary(chunk):
    names = ", ".join(movie_name(e) for e in chunk[:BATCH_NAMES])
    return f"{len(chunk)} movies ({names}{', ...' if len(chunk) > BATCH_NAMES else ''})"


def build_posts(events):
    """One post per movie, except a burst of one kind (a bulk import) collapses into a single summary."""
    by_kind = {}
    for event in events:
        by_kind.setdefault(event["post"], []).append(event)
    posts = []
    for kind, group in by_kind.items():
        bursts = len(group) > BATCH_THRESHOLD
        chunks = [group] if bursts else [[e] for e in group]
        for chunk in chunks:
            first = chunk[0]
            what = burst_summary(chunk) if bursts else movie_name(first)
            posts.append({
                "kind": kind, "what": what, "quality": "" if bursts else first["quality"], "indexer": "" if bursts else first["indexer"],
                "message": first["message"], "marks": [(e["key"], e["detail"]) for e in chunk],
                "max_id": max(e["id"] for e in chunk),
                "message_id": str(uuid.uuid5(NAMESPACE, "|".join(sorted(e["key"] for e in chunk)) + kind)),
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


def search_movies(term):
    return api("GET", "/movie/lookup", {"term": term}) or []


def resolve_defaults():
    """The quality profile id and root folder to add with: the configured ones, else the first Radarr lists."""
    profiles = api("GET", "/qualityprofile") or []
    folders = api("GET", "/rootfolder") or []
    wanted = RADARR_QUALITY_PROFILE.lower()
    profile = next((p for p in profiles if wanted and wanted in (str(p["id"]), p["name"].lower())), None) or (profiles[0] if profiles else None)
    folder = RADARR_ROOT_FOLDER or (folders[0]["path"] if folders else None)
    if profile is None or folder is None:
        raise RuntimeError("radarr has no quality profile or root folder to add into")
    return profile["id"], folder


def add_movie(lookup):
    """Adds a `lookup` result monitored, searching for it right away."""
    profile_id, folder = resolve_defaults()
    body = {
        "title": lookup["title"], "tmdbId": lookup["tmdbId"], "year": lookup.get("year"), "titleSlug": lookup.get("titleSlug"),
        "images": lookup.get("images") or [], "qualityProfileId": profile_id, "rootFolderPath": folder,
        "monitored": True, "minimumAvailability": "released", "addOptions": {"searchForMovie": True},
    }
    return api("POST", "/movie", body=body)
