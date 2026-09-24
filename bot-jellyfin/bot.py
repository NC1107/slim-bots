#!/usr/bin/env python3
"""A slim-m bot that watches a Jellyfin server and posts to one channel when
something new is added: `Movie added: Inception (2010)`, or for a batch of
episodes landing in one library scan, `Breaking Bad: 12 new episodes`.

Run it with a bot token from Space settings -> Bots, a channel id, and a
Jellyfin API key from Jellyfin's own Dashboard -> API Keys:

    SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... \\
        SLIMM_CHANNEL=<channel-uuid> \\
        JELLYFIN_URL=https://your.jellyfin JELLYFIN_API_KEY=... \\
        python3 bot.py

This bot only talks outward: it never listens on slim-m's websocket and
never answers a command, so it needs no `docs/bots/building-bots.md` cursor
of its own on the slim-m side. All of its state is a Jellyfin-side cursor
instead - see "The Jellyfin cursor" below.

The slim-m side of this bot - auth, the REST call, and the idempotent,
retrying `send` - comes from the `slimbots` package (`../slimbots/`), the
same plumbing every template but `bot-ping` shares. It reaches for
`Client.call`'s raw-body form once, to upload a poster as an attachment;
everything Jellyfin-side (auth, paging, the cursor) is this bot's own, since
`slimbots` has no opinion on anything but slim-m.

## Cold start

The first run does not post anything. It looks up the newest item already
in the library, silently records every item sharing that exact instant as
already-posted, and starts watching from there. This is the same call
`bot-reminders`' `bootstrap_cursor` makes for a channel it has never
watched: replaying years of an existing library into a channel the first
time the bot runs is exactly the spam this project exists to avoid, not a
feature. A deployment that wants a one-time backfill can do it by hand
(browse the library, post what matters) rather than this bot doing it
for everyone who ever runs it for the first time.

## The Jellyfin cursor

The cursor is a `DateCreated` value, not `DateLastSaved`. Tested live
against a 16,506-item Jellyfin 12 library: `DateLastSaved` is not a real
field (asking for it in `fields` returns nothing) and does not sort
anything (`sortBy=DateLastSaved` came back in what looks like name order,
not newest-first). `sortBy=DateCreated&sortOrder=Descending` does sort
correctly and is what every fetch here uses.

There is no working server-side "only what changed" filter for this.
`minDateLastSaved` is honoured, but `DateLastSaved` bumps on any metadata
resave, not just on add - measured live, a `minDateLastSaved` window
that should have meant "added in the last 3 days" returned 1,754 items,
of which only 219 actually had a `DateCreated` in that window. The other
1,535 (87%) were older items Jellyfin had simply re-saved metadata for,
and a first real run posting all of those would have been exactly the
spam problem this bot exists to avoid. `minDateCreated` looks like the
obvious fix and is silently ignored entirely (same result count with or
without it).

So this instead pages backward from the newest item
(`sortBy=DateCreated&sortOrder=Descending`, `startIndex` stepping by
`PAGE_SIZE`) until it crosses the stored cursor, stops, and only then
decides what is new by comparing each item's own `DateCreated` against
the cursor. `MAX_ITEMS_PER_POLL` caps how far back a single poll will
page.

This was a deliberate choice over keeping `minDateLastSaved` as a "cheap"
prefilter and double-checking `DateCreated` after: that prefilter's cost
is not bounded by how much is actually new, it is bounded by how much of
the library has been re-scanned recently, which can drift back toward
the whole library over time as Jellyfin's own metadata refreshes touch
old items. Paging by `DateCreated` costs requests proportional to actual
growth since the last poll, nothing else, and needs no second date check
against a separately-fetched candidate set.

The boundary is still treated as inclusive, the same way the old
`minDateLastSaved` filter was, because ties happen: a bulk import can
give several items the exact same `DateCreated`. Two things close that
gap:

1. Every item this bot has posted is recorded by id in `posted_items` and
   filtered out of every future fetch, restart included, whether or not
   the cursor has moved past it yet.
2. The cursor itself only ever advances forward
   (`INSERT ... ON CONFLICT DO UPDATE ... WHERE excluded.value > value`,
   the same guard `bot-reminders` uses for its own sync cursor).

The two together mean a crash between posting a batch and recording it
just repeats that one fetch next cycle, recomputes the identical group,
and sends under the identical deterministic message id - the same
idempotent-retry story `docs/bots/building-bots.md` describes for a
client-generated message id, just triggered by a poll loop instead of a
user's retry.

## Batching

A library import can add forty episodes in one Jellyfin library scan.
Forty messages is spam; the rule this bot uses:

- Episodes group by series. A series with exactly one new episode posts
  as that one episode; more than one posts as a single message naming the
  season(s) and episode count, with the series' own poster.
- Audio tracks group by album the same way, because "27 songs added" is
  the same problem in a music library, which is also why `Audio` is not
  in the default type filter - see `JELLYFIN_ITEM_TYPES` below.
- Everything else (movies, mainly) posts one message per item, each with
  its own poster - until more than `JELLYFIN_BATCH_THRESHOLD` of the same
  type land in one poll, at which point they collapse into a single
  summary line instead of a wall of movie cards.

## Jellyfin auth

Authenticates with `Authorization: MediaBrowser Token="<key>"`. Confirmed
live against a Jellyfin 12.0.0 server: `X-Emby-Token`, `X-MediaBrowser-Token`
and an `api_key` query parameter all `401` on this version, only the
`Authorization` header works. This server's own served OpenAPI document
agrees - its one security scheme names `Authorization` as the header, never
`X-Emby-Token` - so the earlier header this bot shipped with was this
bot's own mistake, not something the spec got wrong. Whether an older
Jellyfin version accepts a different header is not tested here; there is
only the one live server available.

## What this deliberately does not do

- **Push instead of poll.** Jellyfin has no first-class "call me back"
  webhook in core; that exists only as the separately-installed Webhook
  plugin, and wiring that in means giving Jellyfin a URL back into
  wherever this bot runs, which is a heavier operational lift than every
  other bot in this repo (all of them are outbound-only). Polling on
  `JELLYFIN_POLL_SECONDS` keeps this bot the same shape as the rest:
  nothing needs to open a port for it.
- **A single global cursor per library.** All watched libraries share one
  cursor, so a fast-growing library's timestamps can, in theory, race
  ahead of a slower one's not-yet-fetched item with an earlier real
  `DateCreated`. `posted_items` is the real backstop either way; this
  would only ever change which poll cycle an item shows up in, never
  whether it shows up.
- **Recovering from a Jellyfin API key that stops working.** A `401` from
  Jellyfin is treated the same way slim-m's own `401` is: the credential
  is gone, so this exits rather than polling a dead key forever.
- **Season posters, or anything past the series' own primary image.** The
  series' poster stands in for every episode-group message, movies use
  their own poster, and albums their own cover; nothing here fetches a
  season-specific image.
- **Retrying a failed image upload separately from the message it
  belongs to.** A poster that fails to fetch or upload just means that
  one message posts without an attachment; the text still goes out.

## Commands, per-library routing, and filtering

Set `SLIMM_COMMAND_CHANNEL` to also open a websocket connection and answer
`!jellyfin search <query>`, `!jellyfin recent [days]` and `!jellyfin help`
there, alongside the polling loop above - unset, this bot behaves exactly
as it always has, poll-only, no websocket at all. Both commands hit
Jellyfin directly rather than reading `posted_items`, so they answer from
the library's own current state, not from what this bot happened to have
posted. `slimbots.limits.Cooldown` guards both against being fired rapidly
enough to hammer Jellyfin, and `slimbots.limits.require_len`/`require_int`
bound the query text and the day count before either ever reaches a
request. `slimbots.AuthorFilter` keeps this from ever answering another bot
in the fleet, the same as every other template built on `slimbots`.

`JELLYFIN_LIBRARY_ROUTES` (`libraryId:channelId,...`) sends a library's own
new-item posts to a channel other than `SLIMM_CHANNEL`; a library not
listed still falls back to `SLIMM_CHANNEL`. `JELLYFIN_EXCLUDE_GENRES`
(comma-separated, case-insensitive) drops items carrying any of those
genres before they are grouped into a post - they are still recorded in
`posted_items` and still advance the cursor, quietly, so an excluded item
is never re-fetched and re-considered on every later poll.

## The embed seam

`render_text(card)` is the one function that turns a card - a plain dict of
`title`, `description`, and the ids/timestamps `send_post` needs - into the
message body this bot posts today. Every `render_*_post` function above
builds a card; none of them format text directly. Once slim-m ships message
embeds, adopting them here should mean writing a `render_embed(card)` next
to this function and switching `send_post` to call it, not touching the
five functions that decide what a card says.
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from slimbots import AuthorFilter, Client, Connection, is_token_revoked, limits, run_forever

SLIMM_URL = os.environ.get("SLIMM_URL", "").rstrip("/")
SLIMM_BOT_TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
SLIMM_CHANNEL = os.environ.get("SLIMM_CHANNEL", "")
SLIMM_COMMAND_CHANNEL = os.environ.get("SLIMM_COMMAND_CHANNEL", "")
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "")
JELLYFIN_ITEM_TYPES = [t for t in os.environ.get("JELLYFIN_ITEM_TYPES", "Movie,Episode").split(",") if t]
JELLYFIN_LIBRARY_IDS = [i for i in os.environ.get("JELLYFIN_LIBRARY_IDS", "").split(",") if i]
JELLYFIN_POLL_SECONDS = int(os.environ.get("JELLYFIN_POLL_SECONDS", "300"))
JELLYFIN_BATCH_THRESHOLD = int(os.environ.get("JELLYFIN_BATCH_THRESHOLD", "3"))
DB_PATH = os.environ.get("SLIMM_DB_PATH", "jellyfin_watch.db")
MAX_ITEMS_PER_POLL = 500
PAGE_SIZE = 200
OVERVIEW_MAX_CHARS = 220
# Named so a CDN in front of either server can tell this apart from a browser.
USER_AGENT = "slimm-bot-jellyfin/1.0"
NAMESPACE = uuid.UUID("6e6f6220-6a65-6c6c-7966-696e2d626f74")

FIELDS = (
    "Overview,DateCreated,Genres,SeriesId,SeriesName,SeasonName,"
    "ParentIndexNumber,IndexNumber,AlbumId,Album,AlbumArtist"
)

COMMAND_COOLDOWN_SECONDS = 20
MAX_SEARCH_RESULTS = 8
MAX_QUERY_LENGTH = 100
RECENT_DEFAULT_DAYS = 7
RECENT_MAX_DAYS = 30

TRIGGER_SEARCH = re.compile(r"^!jellyfin\s+search\s+(\S[\s\S]*)$", re.IGNORECASE)
TRIGGER_RECENT = re.compile(r"^!jellyfin\s+recent(?:\s+(\d+))?\s*$", re.IGNORECASE)
TRIGGER_HELP = re.compile(r"^!jellyfin\s+help\s*$", re.IGNORECASE)
TRIGGER_ANY = re.compile(r"^!jellyfin\b", re.IGNORECASE)

HELP_TEXT = (
    "commands: `!jellyfin search <query>`, "
    f"`!jellyfin recent [days]` (default {RECENT_DEFAULT_DAYS}, max {RECENT_MAX_DAYS})."
)


def parse_library_routes(spec):
    """`"libraryId:channelId,..."` -> `{library_id: channel_id}`. A library
    not present here still posts to `SLIMM_CHANNEL`."""
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


JELLYFIN_LIBRARY_ROUTES = parse_library_routes(os.environ.get("JELLYFIN_LIBRARY_ROUTES", ""))
JELLYFIN_EXCLUDE_GENRES = {
    g.strip().lower() for g in os.environ.get("JELLYFIN_EXCLUDE_GENRES", "").split(",") if g.strip()
}


def target_channel(library_id):
    return JELLYFIN_LIBRARY_ROUTES.get(library_id, SLIMM_CHANNEL)


def is_excluded(item):
    if not JELLYFIN_EXCLUDE_GENRES:
        return False
    genres = {g.lower() for g in (item.get("Genres") or [])}
    return bool(genres & JELLYFIN_EXCLUDE_GENRES)


class JellyfinAuthError(Exception):
    """The Jellyfin API key was rejected. Not something to retry."""


def jellyfin_auth_header():
    """`Authorization: MediaBrowser Token="..."` - confirmed live as the only
    form of the four commonly documented ones that this Jellyfin version
    accepts; see the module docstring's Jellyfin auth."""
    return f'MediaBrowser Token="{JELLYFIN_API_KEY}"'


def jf_get(path, params=None):
    """One authenticated Jellyfin GET, returning parsed JSON."""
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
    """One authenticated Jellyfin GET for raw bytes, or None on any failure -
    a missing poster should never block the message it belongs to."""
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
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS posted_items (
            item_id TEXT PRIMARY KEY,
            posted_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def get_cursor(conn):
    row = conn.execute("SELECT value FROM state WHERE key = 'cursor'").fetchone()
    return row[0] if row else None


def advance_cursor(conn, value):
    """Only ever moves the cursor forward; a lexicographic compare on a
    fixed-width ISO 8601 UTC string sorts the same as a chronological one,
    so this needs no date parsing at all."""
    conn.execute(
        "INSERT INTO state (key, value) VALUES ('cursor', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value "
        "WHERE excluded.value > state.value",
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
    """A cold start: find the newest item already in the library, silently
    mark everything sharing that exact instant as already-posted, and
    start watching from there. See the module docstring's Cold start."""
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
        "recursive": "true",
        "sortBy": "DateCreated",
        "sortOrder": "Descending",
        "fields": FIELDS,
        "includeItemTypes": ",".join(JELLYFIN_ITEM_TYPES) if JELLYFIN_ITEM_TYPES else None,
        "startIndex": start_index,
        "limit": limit,
    }
    if library_id:
        params["parentId"] = library_id
    params = {k: v for k, v in params.items() if v is not None}
    result = jf_get("/Items", params)
    return result.get("Items", [])


def newest_item():
    """The single newest item across every configured library, or None for
    an empty one. One page-1 request per library."""
    newest = None
    for library_id in JELLYFIN_LIBRARY_IDS or [None]:
        page = fetch_page(library_id, 0, 1)
        if page and (newest is None or page[0]["DateCreated"] > newest["DateCreated"]):
            newest = page[0]
    return newest


def _items_since_in_library(library_id, cursor):
    """One library's page-by-page walk backward from its newest item, until
    an item's `DateCreated` falls below `cursor` or `MAX_ITEMS_PER_POLL` is
    reached. Split out of `items_since` so that function's own job - merging
    this across every configured library - reads as one loop, not two nested
    ones plus the page-crossing logic in between."""
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
    """Pages backward from the newest item in each configured library until
    an item's `DateCreated` falls below `cursor`, merging across libraries
    and deduplicating by id. See the module docstring's Jellyfin cursor for
    why this walks pages instead of filtering on `minDateLastSaved`."""
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
    """Groups new items into the messages this poll cycle will send, in the
    order their earliest item was first seen - see the module docstring's
    Batching section for the rule."""
    groups = {}
    order = []
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
    return make_card(
        title, trimmed_overview(item), [item["Id"]], item["DateCreated"], poster_item_id=item["Id"], library_id=item.get("_library_id")
    )


def render_series_post(episodes):
    series_name = episodes[0].get("SeriesName") or "Unknown series"
    if len(episodes) == 1:
        return render_episode_post(episodes[0], series_name)

    by_season = {}
    season_order = []
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
        f"{series_name}: {len(episodes)} new episodes",
        "\n".join(lines),
        [e["Id"] for e in episodes],
        max_created,
        poster_item_id=episodes[0]["SeriesId"],
        library_id=episodes[0].get("_library_id"),
    )


def render_episode_post(episode, series_name):
    season = episode.get("SeasonName") or ""
    number = episode.get("IndexNumber")
    tag = f"{season} episode {number}" if number is not None else season
    title = episode.get("Name") or ""
    label = f"New episode: {series_name} - {tag}" + (f' "{title}"' if title else "")
    return make_card(
        label,
        None,
        [episode["Id"]],
        episode["DateCreated"],
        poster_item_id=episode.get("SeriesId"),
        library_id=episode.get("_library_id"),
    )


def render_album_post(tracks):
    artist = tracks[0].get("AlbumArtist") or "Unknown artist"
    album = tracks[0].get("Album") or "Unknown album"
    if len(tracks) == 1:
        title = f"Track added: {artist} - {tracks[0]['Name']} ({album})"
    else:
        title = f"Album added: {artist} - {album} ({len(tracks)} tracks)"
    max_created = max(t["DateCreated"] for t in tracks)
    return make_card(
        title,
        None,
        [t["Id"] for t in tracks],
        max_created,
        poster_item_id=tracks[0]["AlbumId"],
        library_id=tracks[0].get("_library_id"),
    )


def render_collapsed_post(item_type, items):
    plural = f"{item_type}s" if not item_type.endswith("s") else item_type
    names = [item["Name"] for item in items[:3]]
    rest = len(items) - len(names)
    named = ", ".join(names) + (f" and {rest} more" if rest > 0 else "")
    max_created = max(item["DateCreated"] for item in items)
    return make_card(
        f"{len(items)} {plural} added: {named}",
        None,
        [item["Id"] for item in items],
        max_created,
        poster_item_id=None,
        library_id=items[0].get("_library_id"),
    )


def make_card(title, description, item_ids, max_created, poster_item_id, library_id):
    """A plain dict describing one message: a title, an optional
    description, and what `send_post` needs to post and track it. See the
    module docstring's "The embed seam" for what this is for."""
    return {
        "title": title,
        "description": description,
        "item_ids": item_ids,
        "max_created": max_created,
        "poster_item_id": poster_item_id,
        "library_id": library_id,
        "message_id": str(uuid.uuid5(NAMESPACE, ",".join(sorted(item_ids)))),
    }


def render_text(card):
    """Renders a card as the plain-text message body this bot posts today -
    the seam a future `render_embed(card)` replaces. See the module
    docstring."""
    return f"{card['title']}\n{card['description']}" if card.get("description") else card["title"]


def upload_poster(client, poster_item_id):
    """Fetches a Jellyfin item's primary image and re-hosts it as a slim-m
    attachment, or None if either step fails - a missing poster is never
    fatal to the message it would have illustrated."""
    if not poster_item_id:
        return None
    image_bytes = jf_get_bytes(f"/Items/{poster_item_id}/Images/Primary")
    if not image_bytes:
        return None
    try:
        attachment = client.call(
            "POST",
            "/attachments?filename=poster.jpg",
            raw_body=image_bytes,
            headers={"content-type": "application/octet-stream"},
        )
        return attachment["id"] if attachment else None
    except urllib.error.HTTPError as err:
        if is_token_revoked(err):
            raise
        return None


def send_post(client, entry):
    attachment_id = upload_poster(client, entry["poster_item_id"])
    attachment_ids = [attachment_id] if attachment_id else None
    channel_id = target_channel(entry.get("library_id"))
    client.send(channel_id, render_text(entry), message_id=entry["message_id"], attachment_ids=attachment_ids)


def poll_once(client, conn):
    items = fetch_new_items(conn)
    if not items:
        return
    excluded = [item for item in items if is_excluded(item)]
    if excluded:
        mark_posted(conn, [item["Id"] for item in excluded], quiet=True)
        advance_cursor(conn, max(item["DateCreated"] for item in excluded))
    postable = [item for item in items if not is_excluded(item)]
    for entry in build_posts(postable):
        try:
            send_post(client, entry)
        except Exception as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            break
        mark_posted(conn, entry["item_ids"])
        advance_cursor(conn, entry["max_created"])


async def poll_loop(client, conn):
    while True:
        try:
            poll_once(client, conn)
        except JellyfinAuthError:
            print("jellyfin api key rejected - exiting", file=sys.stderr)
            return 1
        except Exception as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                return 1
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        await asyncio.sleep(JELLYFIN_POLL_SECONDS)


def search_items(query, limit):
    params = {
        "searchTerm": query,
        "recursive": "true",
        "fields": FIELDS,
        "includeItemTypes": ",".join(JELLYFIN_ITEM_TYPES) if JELLYFIN_ITEM_TYPES else None,
        "limit": limit,
    }
    params = {k: v for k, v in params.items() if v is not None}
    return jf_get("/Items", params).get("Items", [])


def run_search(client, cooldown, author_id, query, reply_to_id):
    wait_message = cooldown.check(author_id)
    if wait_message:
        client.send(SLIMM_COMMAND_CHANNEL, wait_message, reply_to_id=reply_to_id)
        return
    try:
        query = limits.require_len(query.strip(), max_len=MAX_QUERY_LENGTH, field="a search query")
    except limits.ValidationError as err:
        client.send(SLIMM_COMMAND_CHANNEL, str(err), reply_to_id=reply_to_id)
        return
    try:
        items = search_items(query, MAX_SEARCH_RESULTS)
    except JellyfinAuthError:
        client.send(SLIMM_COMMAND_CHANNEL, "jellyfin search is unavailable right now.", reply_to_id=reply_to_id)
        return
    if not items:
        client.send(SLIMM_COMMAND_CHANNEL, f'nothing found for "{query}".', reply_to_id=reply_to_id)
        return
    lines = [
        f"- {item.get('Name')}"
        + (f" ({item['ProductionYear']})" if item.get("ProductionYear") else "")
        + f" [{item.get('Type')}]"
        for item in items
    ]
    client.send(SLIMM_COMMAND_CHANNEL, "\n".join(lines), reply_to_id=reply_to_id)


def run_recent(client, cooldown, author_id, days_text, reply_to_id):
    wait_message = cooldown.check(author_id)
    if wait_message:
        client.send(SLIMM_COMMAND_CHANNEL, wait_message, reply_to_id=reply_to_id)
        return
    try:
        days = limits.require_int(
            days_text or str(RECENT_DEFAULT_DAYS), min_value=1, max_value=RECENT_MAX_DAYS, field="days"
        )
    except limits.ValidationError as err:
        client.send(SLIMM_COMMAND_CHANNEL, str(err), reply_to_id=reply_to_id)
        return
    try:
        items = items_since((datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z"))
    except JellyfinAuthError:
        client.send(SLIMM_COMMAND_CHANNEL, "jellyfin is unavailable right now.", reply_to_id=reply_to_id)
        return
    if not items:
        client.send(SLIMM_COMMAND_CHANNEL, f"nothing added in the last {days} day(s).", reply_to_id=reply_to_id)
        return
    counts = {}
    for item in items:
        item_type = item.get("Type", "item")
        counts[item_type] = counts.get(item_type, 0) + 1
    summary = ", ".join(f"{count} {item_type}" for item_type, count in sorted(counts.items()))
    client.send(
        SLIMM_COMMAND_CHANNEL,
        f"{len(items)} item(s) added in the last {days} day(s): {summary}",
        reply_to_id=reply_to_id,
    )


def handle_command(client, authors, cooldown, me, message):
    author_id = message.get("author_id")
    if not authors.should_handle(author_id, me):
        return
    content = (message.get("content") or "").strip()
    reply_to_id = message.get("id")

    if match := TRIGGER_SEARCH.match(content):
        run_search(client, cooldown, author_id, match.group(1), reply_to_id)
    elif match := TRIGGER_RECENT.match(content):
        run_recent(client, cooldown, author_id, match.group(1), reply_to_id)
    elif TRIGGER_HELP.match(content) or TRIGGER_ANY.match(content):
        client.send(SLIMM_COMMAND_CHANNEL, HELP_TEXT, reply_to_id=reply_to_id)


async def command_loop(client):
    """Only runs when SLIMM_COMMAND_CHANNEL is set - unset, this coroutine
    returns immediately and the bot behaves exactly as it always has."""
    if not SLIMM_COMMAND_CHANNEL:
        return 0
    authors = AuthorFilter(client)
    cooldown = limits.Cooldown(COMMAND_COOLDOWN_SECONDS)

    async def attempt(reset_delay):
        me = client.me()["id"]
        async with await Connection.open(client) as socket:
            print("commands: listening", flush=True)
            reset_delay()
            async for frame in socket.frames():
                # Ignore a frame type we do not know; see bot-ping's docstring.
                if frame.get("type") != "message.created" or frame.get("channel_id") != SLIMM_COMMAND_CHANNEL:
                    continue
                message = frame.get("message") or {}
                handle_command(client, authors, cooldown, me, message)

    return await run_forever(attempt)


def check_config():
    missing = [
        name
        for name, value in (
            ("SLIMM_URL", SLIMM_URL),
            ("SLIMM_BOT_TOKEN", SLIMM_BOT_TOKEN),
            ("SLIMM_CHANNEL", SLIMM_CHANNEL),
            ("JELLYFIN_URL", JELLYFIN_URL),
            ("JELLYFIN_API_KEY", JELLYFIN_API_KEY),
        )
        if not value
    ]
    if missing:
        print(f"missing required environment: {', '.join(missing)}", file=sys.stderr)
        return False
    for name, url in (("SLIMM_URL", SLIMM_URL), ("JELLYFIN_URL", JELLYFIN_URL)):
        loopback = urllib.parse.urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1")
        if not url.startswith("https://") and not loopback:
            print(f"refusing a plaintext, non-loopback {name}: a token on the wire is a leak", file=sys.stderr)
            return False
    return True


async def main():
    if not check_config():
        return 2

    client = Client(SLIMM_URL, SLIMM_BOT_TOKEN, USER_AGENT)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    try:
        me = client.me()
        print(f"slim-m: connected as {me['id']}", flush=True)
        info = jf_get("/System/Info")
        print(f"jellyfin: connected to {info.get('ServerName')} {info.get('Version')}", flush=True)
        bootstrap_cursor(conn)
    except JellyfinAuthError:
        print("jellyfin api key rejected at startup - exiting", file=sys.stderr)
        return 1
    except Exception as err:
        if is_token_revoked(err):
            print("slimm bot token rejected at startup - exiting", file=sys.stderr)
            return 1
        print(f"could not reach slim-m or jellyfin at startup: {type(err).__name__}: {err}", file=sys.stderr)
        return 2

    print(f"watching {', '.join(JELLYFIN_ITEM_TYPES) or 'no types (misconfigured)'}, polling every {JELLYFIN_POLL_SECONDS}s", flush=True)
    if SLIMM_COMMAND_CHANNEL:
        print(f"commands enabled on {SLIMM_COMMAND_CHANNEL}", flush=True)

    tasks = [asyncio.create_task(poll_loop(client, conn))]
    if SLIMM_COMMAND_CHANNEL:
        tasks.append(asyncio.create_task(command_loop(client)))

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        result = task.result()
        if result:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()) or 0)
