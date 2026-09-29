"""bot-radarr's Radarr api calls, history mapping and post grouping; the shared machinery lives in bots/arrkit."""

import uuid

from arrkit import arr, history, store
from arrkit.service import AuthError, Service  # noqa: F401 - re-exported for the cog and bot

NAMESPACE = uuid.UUID("72616461-7272-4000-8000-626f74000000")
SERVICE = Service("radarr", "/api/v3")
HISTORY_INCLUDE = {"includeMovie": "true"}
BATCH_THRESHOLD = 5
BATCH_NAMES = 5
QUALITY_PROFILE = ""
ROOT_FOLDER = ""

init_db = store.init_db


def configure(bot):
    """Resolves the RADARR_* settings through `bot.setting()`; called once, right after `Bot()` is built."""
    global QUALITY_PROFILE, ROOT_FOLDER
    SERVICE.configure(bot)
    QUALITY_PROFILE = bot.setting("RADARR_QUALITY_PROFILE", "") or ""
    ROOT_FOLDER = bot.setting("RADARR_ROOT_FOLDER", "") or ""


def api(method, path, params=None, body=None):
    return SERVICE.call(method, path, params, body)


def fetch_history_since(cursor):
    return history.fetch_since(api, cursor, HISTORY_INCLUDE)


def bootstrap_cursor(conn):
    history.bootstrap(conn, api)


def _ident(record):
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
        "ident": _ident(record), "title": movie.get("title") or "Unknown movie", "year": movie.get("year"),
        "quality": history.quality_of(record), "indexer": data.get("indexer") or "", "message": data.get("message") or "",
        "source": record.get("sourceTitle") or "", "id": record["id"],
    }


def plan_events(conn, records):
    upgraded = history.upgrade_idents(records, "movieFileDeleted", _ident)
    return history.plan_events(conn, records, _event_from, upgraded)


def movie_name(event):
    return f"{event['title']} ({event['year']})" if event["year"] else event["title"]


def burst_summary(group):
    names = ", ".join(movie_name(e) for e in group[:BATCH_NAMES])
    return f"{len(group)} movies ({names}{', ...' if len(group) > BATCH_NAMES else ''})"


def build_posts(events):
    """One post per movie, except a burst of one kind (a bulk import) collapses into a single summary."""
    by_kind = {}
    for event in events:
        by_kind.setdefault(event["post"], []).append(event)
    posts = []
    for kind, group in by_kind.items():
        bursts = len(group) > BATCH_THRESHOLD
        for chunk in ([group] if bursts else [[e] for e in group]):
            first = chunk[0]
            posts.append(history.make_post(
                NAMESPACE, kind, burst_summary(chunk) if bursts else movie_name(first), chunk,
                quality="" if bursts else first["quality"], indexer="" if bursts else first["indexer"], message=first["message"],
            ))
    return sorted(posts, key=lambda p: p["max_id"])


def lookup(term):
    return api("GET", "/movie/lookup", {"term": term}) or []


def add(lookup_result):
    """Adds a `lookup` result monitored, searching for it right away."""
    profile_id, folder = arr.resolve_defaults(api, "radarr", QUALITY_PROFILE, ROOT_FOLDER)
    body = {
        "title": lookup_result["title"], "tmdbId": lookup_result["tmdbId"], "year": lookup_result.get("year"),
        "titleSlug": lookup_result.get("titleSlug"), "images": lookup_result.get("images") or [],
        "qualityProfileId": profile_id, "rootFolderPath": folder, "monitored": True,
        "minimumAvailability": "released", "addOptions": {"searchForMovie": True},
    }
    return api("POST", "/movie", body=body)
