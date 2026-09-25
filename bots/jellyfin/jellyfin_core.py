"""bot-jellyfin's settings, Jellyfin API client, and post grouping/rendering; see docs/framework.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" there for why."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from slimbots import Embed
from slimbots.limits import Cooldown

MAX_ITEMS_PER_POLL = 500
PAGE_SIZE = 200
OVERVIEW_MAX_CHARS = 220
# Named so a CDN in front of either server can tell this apart from a browser.
USER_AGENT = "slimm-bot-jellyfin/1.0"
NAMESPACE = uuid.UUID("6e6f6220-6a65-6c6c-7966-696e2d626f74")

FIELDS = "Overview,DateCreated,Genres,SeriesId,SeriesName,SeasonName,ParentIndexNumber,IndexNumber,AlbumId,Album,AlbumArtist"

COMMAND_COOLDOWN_SECONDS = 20
MAX_SEARCH_RESULTS = 8
MAX_QUERY_LENGTH = 100
RECENT_DEFAULT_DAYS = 7
RECENT_MAX_DAYS = 30

HELP_TEXT = (
    "commands: `!jellyfin search <query>`, "
    f"`!jellyfin recent [days]` (default {RECENT_DEFAULT_DAYS}, max {RECENT_MAX_DAYS})."
)

# What `!watch`/`!subs` need beyond FIELDS: total runtime, and the audio/subtitle track list.
STREAM_FIELDS = "RunTimeTicks,MediaStreams"

# Populated once by configure(); a bot script calls it right after building Bot().
JELLYFIN_URL = ""
JELLYFIN_API_KEY = ""
JELLYFIN_ITEM_TYPES = []
JELLYFIN_LIBRARY_IDS = []
JELLYFIN_POLL_SECONDS = 300
JELLYFIN_BATCH_THRESHOLD = 3
JELLYFIN_LIBRARY_ROUTES = {}
JELLYFIN_EXCLUDE_GENRES = set()
JELLYFIN_STREAM_WIDTH = 1280
JELLYFIN_STREAM_HEIGHT = 720
JELLYFIN_STREAM_FPS = 30
JELLYFIN_STREAM_MAX_BITRATE = 8_000_000
# Reused as the WebRTC ceiling too by default - the transcode's own bitrate, not a guess; see README.md.
JELLYFIN_STREAM_WEBRTC_MAX_BITRATE = None
JELLYFIN_STREAM_AUDIO_MAX_BITRATE = 128_000

_command_cooldown = Cooldown(COMMAND_COOLDOWN_SECONDS)


def parse_library_routes(spec):
    """`"libraryId:channelId,..."` -> `{library_id: channel_id}`; a library not listed posts to `bot.channel`."""
    routes = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        library_id, _, channel_id = pair.partition(":")
        if not library_id or not channel_id:
            raise RuntimeError(f"bad JELLYFIN_LIBRARY_ROUTES entry: {pair!r}")
        routes[library_id.strip()] = channel_id.strip()
    return routes


def configure(bot):
    """Resolves every JELLYFIN_* setting through `bot.setting()`; called once, right after `Bot()` is built."""
    global JELLYFIN_URL, JELLYFIN_API_KEY, JELLYFIN_ITEM_TYPES, JELLYFIN_LIBRARY_IDS
    global JELLYFIN_POLL_SECONDS, JELLYFIN_BATCH_THRESHOLD, JELLYFIN_LIBRARY_ROUTES, JELLYFIN_EXCLUDE_GENRES
    global JELLYFIN_STREAM_WIDTH, JELLYFIN_STREAM_HEIGHT, JELLYFIN_STREAM_FPS, JELLYFIN_STREAM_MAX_BITRATE
    global JELLYFIN_STREAM_WEBRTC_MAX_BITRATE, JELLYFIN_STREAM_AUDIO_MAX_BITRATE
    JELLYFIN_URL = (bot.setting("JELLYFIN_URL", required=True) or "").rstrip("/")
    JELLYFIN_API_KEY = bot.setting("JELLYFIN_API_KEY", required=True) or ""
    JELLYFIN_ITEM_TYPES = bot.setting("JELLYFIN_ITEM_TYPES", ["Movie", "Episode"], type=list)
    JELLYFIN_LIBRARY_IDS = bot.setting("JELLYFIN_LIBRARY_IDS", [], type=list)
    JELLYFIN_POLL_SECONDS = bot.setting("JELLYFIN_POLL_SECONDS", 300, type=int)
    JELLYFIN_BATCH_THRESHOLD = bot.setting("JELLYFIN_BATCH_THRESHOLD", 3, type=int)
    JELLYFIN_LIBRARY_ROUTES = parse_library_routes(bot.setting("JELLYFIN_LIBRARY_ROUTES", "") or "")
    JELLYFIN_EXCLUDE_GENRES = {g.lower() for g in bot.setting("JELLYFIN_EXCLUDE_GENRES", [], type=list)}
    JELLYFIN_STREAM_WIDTH = bot.setting("JELLYFIN_STREAM_WIDTH", 1280, type=int)
    JELLYFIN_STREAM_HEIGHT = bot.setting("JELLYFIN_STREAM_HEIGHT", 720, type=int)
    JELLYFIN_STREAM_FPS = bot.setting("JELLYFIN_STREAM_FPS", 30, type=int)
    JELLYFIN_STREAM_MAX_BITRATE = bot.setting("JELLYFIN_STREAM_MAX_BITRATE", 8_000_000, type=int)
    JELLYFIN_STREAM_WEBRTC_MAX_BITRATE = bot.setting("JELLYFIN_STREAM_WEBRTC_MAX_BITRATE", None, type=int) or JELLYFIN_STREAM_MAX_BITRATE
    JELLYFIN_STREAM_AUDIO_MAX_BITRATE = bot.setting("JELLYFIN_STREAM_AUDIO_MAX_BITRATE", 128_000, type=int)


def check_jellyfin_config():
    """A plaintext, non-loopback JELLYFIN_URL leaks the API key; presence is `bot.setting`'s job, not this one's."""
    if not JELLYFIN_URL:
        return None
    loopback = urllib.parse.urlsplit(JELLYFIN_URL).hostname in ("localhost", "127.0.0.1", "::1")
    if not JELLYFIN_URL.startswith("https://") and not loopback:
        return "refusing a plaintext, non-loopback JELLYFIN_URL: a token on the wire is a leak"
    return None


def target_channel(bot, library_id):
    return JELLYFIN_LIBRARY_ROUTES.get(library_id) or bot.channel


def is_excluded(item):
    if not JELLYFIN_EXCLUDE_GENRES:
        return False
    genres = {g.lower() for g in (item.get("Genres") or [])}
    return bool(genres & JELLYFIN_EXCLUDE_GENRES)


class JellyfinAuthError(Exception):
    """The Jellyfin API key was rejected. Not something to retry."""


def jellyfin_auth_header():
    """`Authorization: MediaBrowser Token="..."` - the one form this Jellyfin version accepts; see README.md."""
    return f'MediaBrowser Token="{JELLYFIN_API_KEY}"'


def jf_get(path, params=None):
    """One authenticated Jellyfin GET, returning parsed JSON. Sync (urllib); called via asyncio.to_thread."""
    query = urllib.parse.urlencode(params or {}, doseq=True)
    request = urllib.request.Request(f"{JELLYFIN_URL}{path}?{query}")
    request.add_header("authorization", jellyfin_auth_header())
    request.add_header("user-agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise JellyfinAuthError("jellyfin api key rejected") from err
        raise


def jf_get_bytes(path):
    """One authenticated Jellyfin GET for raw bytes, or None - a missing poster never blocks its message."""
    request = urllib.request.Request(f"{JELLYFIN_URL}{path}")
    request.add_header("authorization", jellyfin_auth_header())
    request.add_header("user-agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except Exception:
        return None


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS posted_items (item_id TEXT PRIMARY KEY, posted_at INTEGER NOT NULL);
        """
    )
    conn.commit()


def get_cursor(conn):
    row = conn.execute("SELECT value FROM state WHERE key = 'cursor'").fetchone()
    return row[0] if row else None


def advance_cursor(conn, value):
    """Only ever moves forward; a lexicographic compare on a fixed-width ISO 8601 UTC string sorts chronologically too."""
    conn.execute(
        "INSERT INTO state (key, value) VALUES ('cursor', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE excluded.value > state.value",
        (value,),
    )
    conn.commit()


def already_posted(conn, item_id):
    return conn.execute("SELECT 1 FROM posted_items WHERE item_id = ?", (item_id,)).fetchone() is not None


def mark_posted(conn, item_ids, quiet=False):
    now = int(time.time())
    conn.executemany(
        "INSERT OR IGNORE INTO posted_items (item_id, posted_at) VALUES (?, ?)",
        [(item_id, 0 if quiet else now) for item_id in item_ids],
    )
    conn.commit()


def bootstrap_cursor(conn):
    """A cold start: mark everything at the newest instant already-posted and start watching from there; see README.md."""
    if get_cursor(conn) is not None:
        return
    newest = newest_item()
    if newest is None:
        advance_cursor(conn, "0001-01-01T00:00:00.0000000Z")
        return
    newest_at = newest["DateCreated"]
    boundary = items_since(newest_at)
    mark_posted(conn, [item["Id"] for item in boundary], quiet=True)
    advance_cursor(conn, newest_at)


def fetch_page(library_id, start_index, limit):
    """One `/Items` page, newest `DateCreated` first."""
    params = {
        "recursive": "true", "sortBy": "DateCreated", "sortOrder": "Descending", "fields": FIELDS,
        "includeItemTypes": ",".join(JELLYFIN_ITEM_TYPES) if JELLYFIN_ITEM_TYPES else None,
        "startIndex": start_index, "limit": limit,
    }
    if library_id:
        params["parentId"] = library_id
    params = {k: v for k, v in params.items() if v is not None}
    return jf_get("/Items", params).get("Items", [])


def newest_item():
    """The single newest item across every configured library, or None for an empty one."""
    newest = None
    for library_id in JELLYFIN_LIBRARY_IDS or [None]:
        page = fetch_page(library_id, 0, 1)
        if page and (newest is None or page[0]["DateCreated"] > newest["DateCreated"]):
            newest = page[0]
    return newest


def _items_since_in_library(library_id, cursor):
    """One library's page-by-page walk backward until an item's `DateCreated` falls below `cursor`; see README.md."""
    collected = {}
    start_index = 0
    while len(collected) < MAX_ITEMS_PER_POLL:
        page = fetch_page(library_id, start_index, PAGE_SIZE)
        if not page:
            return collected
        crossed_cursor = False
        for item in page:
            if item["DateCreated"] < cursor:
                crossed_cursor = True
                break
            item["_library_id"] = library_id
            collected[item["Id"]] = item
        if crossed_cursor or len(page) < PAGE_SIZE:
            return collected
        start_index += PAGE_SIZE
    return collected


def items_since(cursor):
    """Pages backward from the newest item in each configured library until `DateCreated` falls below `cursor`."""
    by_id = {}
    for library_id in JELLYFIN_LIBRARY_IDS or [None]:
        by_id.update(_items_since_in_library(library_id, cursor))
    return sorted(by_id.values(), key=lambda item: item["DateCreated"])


def fetch_new_items(conn):
    cursor = get_cursor(conn) or ""
    items = items_since(cursor)
    return [item for item in items if not already_posted(conn, item["Id"])]


def group_key(item):
    kind = item.get("Type")
    if kind == "Episode" and item.get("SeriesId"):
        return ("series", item["SeriesId"])
    if kind == "Audio" and item.get("AlbumId"):
        return ("album", item["AlbumId"])
    return ("single", item["Id"])


def build_posts(items):
    """Groups new items into this poll cycle's messages, in first-seen order; see README.md's batching rule."""
    groups, order = {}, []
    for item in items:
        key = group_key(item)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    posts = []
    singles_by_type = {}
    for key in order:
        kind = key[0]
        group = groups[key]
        if kind == "series":
            posts.append(render_series_post(group))
        elif kind == "album":
            posts.append(render_album_post(group))
        else:
            singles_by_type.setdefault(group[0]["Type"], []).append(group[0])

    for item_type, group in singles_by_type.items():
        if len(group) > JELLYFIN_BATCH_THRESHOLD:
            posts.append(render_collapsed_post(item_type, group))
        else:
            posts.extend(render_single_post(item) for item in group)
    return posts


def trimmed_overview(item):
    overview = (item.get("Overview") or "").strip()
    if len(overview) <= OVERVIEW_MAX_CHARS:
        return overview
    return overview[:OVERVIEW_MAX_CHARS].rsplit(" ", 1)[0] + "..."


def render_single_post(item):
    item_type = item.get("Type")
    year = item.get("ProductionYear")
    title = f"{item_type} added: {item['Name']}" + (f" ({year})" if year else "")
    return make_card(title, trimmed_overview(item), [item["Id"]], item["DateCreated"], item["Id"], item.get("_library_id"))


def render_series_post(episodes):
    series_name = episodes[0].get("SeriesName") or "Unknown series"
    if len(episodes) == 1:
        return render_episode_post(episodes[0], series_name)

    by_season, season_order = {}, []
    for episode in episodes:
        season = episode.get("SeasonName") or "Unknown season"
        if season not in by_season:
            by_season[season] = []
            season_order.append(season)
        by_season[season].append(episode)

    lines = []
    for season in season_order:
        eps = sorted(e.get("IndexNumber") or 0 for e in by_season[season])
        span = f"episode {eps[0]}" if len(eps) == 1 else f"episodes {eps[0]}-{eps[-1]}"
        lines.append(f"{season}: {len(by_season[season])} ({span})")
    max_created = max(e["DateCreated"] for e in episodes)
    return make_card(
        f"{series_name}: {len(episodes)} new episodes", "\n".join(lines), [e["Id"] for e in episodes],
        max_created, episodes[0]["SeriesId"], episodes[0].get("_library_id"),
    )


def render_episode_post(episode, series_name):
    season = episode.get("SeasonName") or ""
    number = episode.get("IndexNumber")
    tag = f"{season} episode {number}" if number is not None else season
    title = episode.get("Name") or ""
    label = f"New episode: {series_name} - {tag}" + (f' "{title}"' if title else "")
    return make_card(label, None, [episode["Id"]], episode["DateCreated"], episode.get("SeriesId"), episode.get("_library_id"))


def render_album_post(tracks):
    artist = tracks[0].get("AlbumArtist") or "Unknown artist"
    album = tracks[0].get("Album") or "Unknown album"
    if len(tracks) == 1:
        title = f"Track added: {artist} - {tracks[0]['Name']} ({album})"
    else:
        title = f"Album added: {artist} - {album} ({len(tracks)} tracks)"
    max_created = max(t["DateCreated"] for t in tracks)
    return make_card(title, None, [t["Id"] for t in tracks], max_created, tracks[0]["AlbumId"], tracks[0].get("_library_id"))


def render_collapsed_post(item_type, items):
    plural = f"{item_type}s" if not item_type.endswith("s") else item_type
    names = [item["Name"] for item in items[:3]]
    rest = len(items) - len(names)
    named = ", ".join(names) + (f" and {rest} more" if rest > 0 else "")
    max_created = max(item["DateCreated"] for item in items)
    return make_card(f"{len(items)} {plural} added: {named}", None, [item["Id"] for item in items], max_created, None, items[0].get("_library_id"))


def make_card(title, description, item_ids, max_created, poster_item_id, library_id):
    """A plain dict: what `send_post` needs to post and track one message; see README.md's embed seam."""
    return {
        "title": title, "description": description, "item_ids": item_ids, "max_created": max_created,
        "poster_item_id": poster_item_id, "library_id": library_id,
        "message_id": str(uuid.uuid5(NAMESPACE, ",".join(sorted(item_ids)))),
    }


def render_text(card):
    return f"{card['title']}\n{card['description']}" if card.get("description") else card["title"]


def render_embed(card):
    """`render_text`'s seam as a real `Embed`; the poster stays a separate attachment - see docs/framework.md."""
    return Embed(title=card["title"], description=card.get("description"))


def search_items(query, limit):
    params = {
        "searchTerm": query, "recursive": "true", "fields": FIELDS,
        "includeItemTypes": ",".join(JELLYFIN_ITEM_TYPES) if JELLYFIN_ITEM_TYPES else None, "limit": limit,
    }
    params = {k: v for k, v in params.items() if v is not None}
    return jf_get("/Items", params).get("Items", [])


def fetch_item_for_playback(item_id):
    """One item's `RunTimeTicks` and `MediaStreams` (audio/subtitle track list) - what `!watch`/`!subs` need."""
    items = jf_get("/Items", {"ids": item_id, "fields": STREAM_FIELDS}).get("Items", [])
    return items[0] if items else None


def subtitle_streams(item):
    return [s for s in item.get("MediaStreams") or [] if s.get("Type") == "Subtitle"]


def find_subtitle_stream(item, language):
    """The first subtitle stream whose language code or display title matches `language`, case-insensitively."""
    language = language.lower()
    for stream in subtitle_streams(item):
        if language in (stream.get("Language") or "").lower():
            return stream
        if language in (stream.get("DisplayTitle") or "").lower():
            return stream
    return None


def build_stream_url(item_id, *, start_seconds=0.0, audio_stream_index=None, subtitle_stream_index=None):
    """A progressive H.264/AAC transcode URL, seekable via `StartTimeTicks`; see README.md's watch-party section.
    `Container=mkv`, not `ts` - a live Jellyfin 12.1 server's `ts` progressive mux silently drops the audio stream."""
    params = {
        "Static": "false", "VideoCodec": "h264", "AudioCodec": "aac", "Container": "mkv",
        "MaxWidth": JELLYFIN_STREAM_WIDTH, "VideoBitrate": JELLYFIN_STREAM_MAX_BITRATE,
        "StartTimeTicks": int(start_seconds * 10_000_000),
    }
    if audio_stream_index is not None:
        params["AudioStreamIndex"] = audio_stream_index
    if subtitle_stream_index is not None:
        params["SubtitleStreamIndex"] = subtitle_stream_index
        params["SubtitleMethod"] = "Encode"
    query = urllib.parse.urlencode(params)
    return f"{JELLYFIN_URL}/Videos/{item_id}/stream?{query}"
