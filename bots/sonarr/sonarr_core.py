"""bot-sonarr's Sonarr api calls, history mapping and post grouping; the shared machinery lives in bots/arrkit."""

import uuid

from arrkit import arr, history, store
from arrkit.service import AuthError, Service  # noqa: F401 - re-exported for the cog and bot

NAMESPACE = uuid.UUID("736f6e61-7272-4000-8000-626f74000000")
SERVICE = Service("sonarr", "/api/v3")
HISTORY_INCLUDE = {"includeSeries": "true", "includeEpisode": "true"}
QUALITY_PROFILE = ""
ROOT_FOLDER = ""

init_db = store.init_db


def configure(bot):
    """Resolves the SONARR_* settings through `bot.setting()`; called once, right after `Bot()` is built."""
    global QUALITY_PROFILE, ROOT_FOLDER
    SERVICE.configure(bot)
    QUALITY_PROFILE = bot.setting("SONARR_QUALITY_PROFILE", "") or ""
    ROOT_FOLDER = bot.setting("SONARR_ROOT_FOLDER", "") or ""


def api(method, path, params=None, body=None):
    return SERVICE.call(method, path, params, body)


def fetch_history_since(cursor):
    return history.fetch_since(api, cursor, HISTORY_INCLUDE)


def bootstrap_cursor(conn):
    history.bootstrap(conn, api)


def episode_label(season, number):
    return f"S{int(season):02d}E{int(number):02d}"


def _ident(record):
    episode = record.get("episode") or {}
    return f"{record['seriesId']}|{episode.get('seasonNumber')}|{episode.get('episodeNumber')}"


def _event_from(record):
    series, episode = record.get("series") or {}, record.get("episode") or {}
    if not series or not episode:
        return None
    data = record.get("data") or {}
    return {
        "ident": _ident(record), "series_id": record["seriesId"], "series": series.get("title") or "Unknown series",
        "year": series.get("year"), "season": episode.get("seasonNumber", 0), "episode": episode.get("episodeNumber", 0),
        "title": episode.get("title") or "", "quality": history.quality_of(record), "indexer": data.get("indexer") or "",
        "message": data.get("message") or "", "source": record.get("sourceTitle") or "", "id": record["id"],
    }


def plan_events(conn, records):
    upgraded = history.upgrade_idents(records, "episodeFileDeleted", _ident)
    return history.plan_events(conn, records, _event_from, upgraded)


def _episode_range(numbers):
    numbers = sorted(set(numbers))
    if len(numbers) > 1 and numbers == list(range(numbers[0], numbers[-1] + 1)):
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
        posts.append(history.make_post(
            NAMESPACE, kind, what, group, quality=first["quality"], indexer=first["indexer"], message=first["message"],
        ))
    return sorted(posts, key=lambda p: p["max_id"])


def lookup(term):
    return api("GET", "/series/lookup", {"term": term}) or []


def add(lookup_result):
    """Adds a `lookup` result monitored, searching for its missing episodes right away."""
    profile_id, folder = arr.resolve_defaults(api, "sonarr", QUALITY_PROFILE, ROOT_FOLDER)
    body = {
        "title": lookup_result["title"], "tvdbId": lookup_result["tvdbId"], "titleSlug": lookup_result.get("titleSlug"),
        "images": lookup_result.get("images") or [], "seasons": lookup_result.get("seasons") or [], "year": lookup_result.get("year"),
        "qualityProfileId": profile_id, "rootFolderPath": folder, "monitored": True, "seasonFolder": True,
        "addOptions": {"monitor": "all", "searchForMissingEpisodes": True},
    }
    return api("POST", "/series", body=body)
