"""bot-seerr's settings, Seerr (Jellyseerr) API client, request-state dedupe and rendering; see docs/framework.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" there for why."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from slimbots.limits import Cooldown

USER_AGENT = "slimm-bot-seerr/1.0"
NAMESPACE = uuid.UUID("73656572-7272-4000-8000-626f74000000")
WATCH_WINDOW = 100
COMMAND_COOLDOWN_SECONDS = 10
MAX_QUERY_LENGTH = 100
MAX_RESULTS = 8
PENDING_LIMIT = 10
USER_SCAN_LIMIT = 200

PENDING, APPROVED, DECLINED = 1, 2, 3
MEDIA_PARTIAL, MEDIA_AVAILABLE = 4, 5
UNAVAILABLE_FOR_REQUEST = (2, 3, 5)

HELP_TEXT = (
    "commands: `!request <title>` (pick from buttons), `!requests` (pending), "
    "`!request link <seerr username>`, `!request unlink`, `!request account`."
)

SEERR_URL = ""
SEERR_API_KEY = ""
SEERR_POLL_SECONDS = 60
SEERR_APPROVER_PERMISSION = "MANAGE_SERVER"
SEERR_DEFAULT_USER_ID = None

_command_cooldown = Cooldown(COMMAND_COOLDOWN_SECONDS)


class SeerrAuthError(Exception):
    """The Seerr API key was rejected. Not something to retry."""


def configure(bot):
    """Resolves every SEERR_* setting through `bot.setting()`; called once, right after `Bot()` is built."""
    global SEERR_URL, SEERR_API_KEY, SEERR_POLL_SECONDS, SEERR_APPROVER_PERMISSION, SEERR_DEFAULT_USER_ID
    SEERR_URL = (bot.setting("SEERR_URL", required=True) or "").rstrip("/")
    SEERR_API_KEY = bot.setting("SEERR_API_KEY", required=True) or ""
    SEERR_POLL_SECONDS = bot.setting("SEERR_POLL_SECONDS", 60, type=int)
    SEERR_APPROVER_PERMISSION = (bot.setting("SEERR_APPROVER_PERMISSION", "MANAGE_SERVER") or "MANAGE_SERVER").upper()
    SEERR_DEFAULT_USER_ID = bot.setting("SEERR_DEFAULT_USER_ID", None, type=int)


def check_seerr_config(permissions):
    """Plaintext only to loopback or a bare docker service name; the approver permission must be a real bit name."""
    if not SEERR_URL:
        return None
    host = urllib.parse.urlsplit(SEERR_URL).hostname or ""
    private = host in ("localhost", "127.0.0.1", "::1") or "." not in host
    if not SEERR_URL.startswith("https://") and not private:
        return "refusing a plaintext SEERR_URL to a public host: the api key on the wire is a leak"
    if not isinstance(getattr(permissions, SEERR_APPROVER_PERMISSION, None), int):
        return f"SEERR_APPROVER_PERMISSION {SEERR_APPROVER_PERMISSION!r} is not a slim-m permission name"
    return None


def api(method, path, params=None, body=None):
    """One authenticated Seerr call returning parsed JSON. Sync (urllib); called via asyncio.to_thread."""
    query = urllib.parse.urlencode(params or {}, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{SEERR_URL}/api/v1{path}?{query}", data=data, method=method)
    request.add_header("x-api-key", SEERR_API_KEY)
    request.add_header("user-agent", USER_AGENT)
    if data is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise SeerrAuthError("seerr api key rejected") from err
        raise


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS announced (key TEXT PRIMARY KEY, at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS user_links (
            slimm_user_id TEXT PRIMARY KEY, seerr_user_id INTEGER NOT NULL, seerr_name TEXT NOT NULL, linked_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def is_announced(conn, key):
    return conn.execute("SELECT 1 FROM announced WHERE key = ?", (key,)).fetchone() is not None


def mark_announced(conn, keys):
    now = int(time.time())
    conn.executemany("INSERT OR IGNORE INTO announced (key, at) VALUES (?, ?)", [(key, now) for key in keys])
    conn.commit()


def get_link(conn, slimm_user_id):
    """`(seerr_user_id, seerr_name)` for a member, or None."""
    row = conn.execute("SELECT seerr_user_id, seerr_name FROM user_links WHERE slimm_user_id = ?", (slimm_user_id,)).fetchone()
    return (row[0], row[1]) if row else None


def owner_of(conn, seerr_user_id):
    row = conn.execute("SELECT slimm_user_id FROM user_links WHERE seerr_user_id = ?", (seerr_user_id,)).fetchone()
    return row[0] if row else None


def set_link(conn, slimm_user_id, seerr_user_id, seerr_name):
    conn.execute(
        "INSERT INTO user_links (slimm_user_id, seerr_user_id, seerr_name, linked_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(slimm_user_id) DO UPDATE SET seerr_user_id = excluded.seerr_user_id, "
        "seerr_name = excluded.seerr_name, linked_at = excluded.linked_at",
        (slimm_user_id, seerr_user_id, seerr_name, int(time.time())),
    )
    conn.commit()


def remove_link(conn, slimm_user_id):
    removed = conn.execute("DELETE FROM user_links WHERE slimm_user_id = ?", (slimm_user_id,)).rowcount
    conn.commit()
    return removed > 0


def user_names(user):
    return {n.lower() for n in (user.get("displayName"), user.get("username"), user.get("jellyfinUsername"), user.get("plexUsername")) if n}


def find_user(name):
    """The Seerr user whose display, jellyfin or plex name is `name` (case-insensitive), else None."""
    wanted = name.strip().lower()
    users = (api("GET", "/user", {"take": USER_SCAN_LIMIT}) or {}).get("results", [])
    return next((u for u in users if wanted in user_names(u)), None)


def display_name(user):
    user = user or {}
    return user.get("displayName") or user.get("username") or user.get("jellyfinUsername") or "someone"


def fetch_recent_requests():
    params = {"take": WATCH_WINDOW, "skip": 0, "filter": "all", "sort": "modified"}
    return (api("GET", "/request", params) or {}).get("results", [])


def media_details(media_type, tmdb_id):
    """Title, year and tmdb link for a request's media, or a placeholder when Seerr cannot say."""
    try:
        data = api("GET", f"/{'tv' if media_type == 'tv' else 'movie'}/{tmdb_id}") or {}
    except urllib.error.HTTPError:
        data = {}
    date = data.get("firstAirDate") or data.get("releaseDate") or ""
    return {"title": data.get("name") or data.get("title") or f"tmdb {tmdb_id}", "year": date[:4]}


def bootstrap(conn, requests):
    """A cold start marks every current state announced, so the existing backlog is never posted."""
    if is_announced(conn, "bootstrap|done"):
        return False
    mark_announced(conn, [key for r in requests for _kind, key in request_events(r)] + ["bootstrap|done"])
    return True


def request_events(request):
    """The dedupe keys and kinds this request's current state has earned, in the order they happened."""
    media = request.get("media") or {}
    rid, status = request["id"], request.get("status")
    events = [("requested", f"r|{rid}|new")]
    if status == APPROVED:
        events.append(("approved", f"r|{rid}|approved"))
    elif status == DECLINED:
        events.append(("declined", f"r|{rid}|declined"))
    base = f"m|{media.get('mediaType')}|{media.get('tmdbId')}"
    if media.get("status") == MEDIA_AVAILABLE:
        events.append(("available", f"{base}|available"))
    elif media.get("status") == MEDIA_PARTIAL:
        events.append(("partial", f"{base}|partial"))
    return events


def plan_posts(conn, requests, details=media_details):
    """Announcements for states not yet announced; a request first seen already approved is one post, not two."""
    posts, availability = [], {}
    for request in sorted(requests, key=lambda r: r["id"]):
        media = request.get("media") or {}
        fresh = [(kind, key) for kind, key in request_events(request) if not is_announced(conn, key)]
        if not fresh:
            continue
        info = details(media.get("mediaType"), media.get("tmdbId"))
        by = display_name(request.get("requestedBy"))
        request_keys = [(kind, key) for kind, key in fresh if kind in ("requested", "approved", "declined")]
        if request_keys:
            kind = request_keys[-1][0]
            posts.append(_post(kind, info, by, request, [k for _, k in request_keys]))
        for kind, key in fresh:
            if kind in ("available", "partial"):
                group = availability.setdefault(key, {"kind": kind, "info": info, "names": [], "request": request})
                group["names"].append(by)
    for key, group in availability.items():
        posts.append(_post(group["kind"], group["info"], ", ".join(sorted(set(group["names"]))), group["request"], [key]))
    return posts


def _post(kind, info, by, request, keys):
    media = request.get("media") or {}
    year = f" ({info['year']})" if info["year"] else ""
    return {
        "kind": kind, "title": f"{info['title']}{year}", "media_type": media.get("mediaType"), "by": by,
        "request_id": request["id"], "keys": keys, "buttons": kind == "requested" and request.get("status") == PENDING,
        "message_id": str(uuid.uuid5(NAMESPACE, "|".join(sorted(keys)) + kind)),
    }


HEADINGS = {"requested": "Requested", "approved": "Approved", "declined": "Declined", "available": "Available", "partial": "Partly available"}


def render_text(post):
    kind = "show" if post["media_type"] == "tv" else "movie"
    return f"{HEADINGS[post['kind']]}: **{post['title']}** [{kind}] - requested by {post['by']}"


def search(term):
    """Movie and tv matches only (Seerr also returns people), best first."""
    results = (api("GET", "/search", {"query": term, "page": 1}) or {}).get("results", [])
    return [r for r in results if r.get("mediaType") in ("movie", "tv")][:MAX_RESULTS]


def result_title(item):
    date = item.get("releaseDate") or item.get("firstAirDate") or ""
    year = f" ({date[:4]})" if date[:4] else ""
    kind = "show" if item.get("mediaType") == "tv" else "movie"
    return f"{item.get('title') or item.get('name') or 'Untitled'}{year} [{kind}]"


def create_request(item, user_id):
    """Files a request for a search result as `user_id`, all seasons for a show."""
    body = {"mediaType": item["mediaType"], "mediaId": item["id"]}
    if item["mediaType"] == "tv":
        body["seasons"] = "all"
    if user_id is not None:
        body["userId"] = user_id
    return api("POST", "/request", body=body)


def set_request_state(request_id, action):
    """`approve` or `decline` a request."""
    return api("POST", f"/request/{int(request_id)}/{action}")


def pending_requests():
    params = {"take": PENDING_LIMIT, "skip": 0, "filter": "pending", "sort": "added"}
    data = api("GET", "/request", params) or {}
    return data.get("results", []), (data.get("pageInfo") or {}).get("results", 0)
