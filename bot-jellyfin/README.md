# bot-jellyfin

A slim-m bot in one file that watches a Jellyfin server and posts to one
channel when something new is added: a movie, a batch of episodes, an
album.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNEL=<channel-uuid> \
JELLYFIN_URL=https://your.jellyfin \
JELLYFIN_API_KEY=... \
python3 bot.py
```

The Jellyfin side is standard library only. The slim-m side uses `slimbots`
for auth, the REST call, and the idempotent `send` - see `requirements.txt`
for why it is pinned to git rather than the published package for now.

`SLIMM_CHANNEL` is the one channel this bot posts into. It needs
`SEND_MESSAGES` and `ATTACH_FILES` there; see "What your bot may do" in
`docs/bots/building-bots.md` for granting a bot a channel overwrite.

`JELLYFIN_API_KEY` comes from Jellyfin's own Dashboard -> API Keys, as a
Jellyfin server administrator. It is a credential exactly like a slim-m bot
token: keep it in the environment, never in source, and this bot never logs
it or puts it in a URL. It goes in an `Authorization: MediaBrowser
Token="..."` header - confirmed against a real Jellyfin 12.0.0 server that
this is the only one of four commonly documented forms that works there:
`X-Emby-Token`, `X-MediaBrowser-Token` and an `api_key` query parameter all
answered `401` on this version, the `Authorization` header alone answered
`200`. Whether an older Jellyfin version accepts a different header is not
tested here - there was only the one live server to test against.

## Other settings

| Variable | Default | What it does |
| --- | --- | --- |
| `JELLYFIN_ITEM_TYPES` | `Movie,Episode` | Comma-separated Jellyfin item types to watch. `Audio` is deliberately not in the default - see Batching. |
| `JELLYFIN_LIBRARY_IDS` | unset (whole server) | Comma-separated library (`parentId`) UUIDs to scope to. Find one by opening the library in Jellyfin's web UI and reading `ParentId` off the URL - `GET /Library/MediaFolders` also lists them, but needs an elevated key, more than this bot otherwise asks for. |
| `JELLYFIN_POLL_SECONDS` | `300` | How often to check Jellyfin for anything new. |
| `JELLYFIN_BATCH_THRESHOLD` | `3` | More than this many ungrouped items (movies, mainly) of the same type in one poll collapse into one summary message instead of one card each. |
| `SLIMM_DB_PATH` | `jellyfin_watch.db` | Where the sqlite cursor and dedupe table live. |

## Cold start

The first run posts nothing. It looks up whatever is currently the newest
item in the watched library or libraries, silently records everything that
shares that exact instant as already-seen, and starts watching forward from
there.

This is the same call `bot-reminders`' `bootstrap_cursor` makes for a
channel it has never watched before: replaying years of an existing library
into a channel the moment this bot is first pointed at it is exactly the
spam problem this bot exists to solve, not a feature to preserve. A
deployment that specifically wants a one-time backfill post can do it by
hand; this bot does not do it automatically for anyone who runs it fresh.

## Batching

A Jellyfin library scan can add forty episodes, or a whole album, in one
pass. The rule:

- **Episodes group by series.** One new episode of a series posts as that
  one episode. More than one posts as a single message naming the season(s)
  and episode count (`Test Show: 3 new episodes` / `Season 1: 2 (episodes
  1-2)`), with the series' own poster - never a season-specific one.
- **Audio tracks group by album** the same way a music library scan can
  drop dozens of tracks in one go, which is also why `Audio` sits outside
  `JELLYFIN_ITEM_TYPES`'s default: opt a music library in with
  `JELLYFIN_ITEM_TYPES=Movie,Episode,Audio` (or `JELLYFIN_LIBRARY_IDS`
  scoped to just that library) rather than have it show up in a channel
  meant for film, uninvited.
- **Everything else posts one message per item** - each with its own
  poster - until more than `JELLYFIN_BATCH_THRESHOLD` of the same type land
  in a single poll, at which point they collapse into one summary line with
  no attachment, rather than a wall of cards.

Grouping and the threshold both apply per poll cycle: an item that arrives
alone gets its own card, but the same item arriving in the same cycle as
four others of the same type is collapsed into that cycle's summary. This
is a corner deliberately cut for simplicity - see below.

## The cursor is `DateCreated`, found by paging, not by filtering

An earlier version of this bot asked Jellyfin for `minDateLastSaved >=
<cursor>` as a server-side "only what's new" filter and sorted by
`DateLastSaved`. Both were wrong, found by testing against the owner's real
16,506-item Jellyfin 12 library:

- `DateLastSaved` is not a real field - requesting it in `fields` returns
  nothing - and `sortBy=DateLastSaved` does not sort by anything
  date-related; it came back in what looks like name order.
- `minDateLastSaved` is honoured as a filter, but `DateLastSaved` bumps on
  any metadata resave, not just on add. A window meant to mean "added in
  the last 3 days" returned 1,754 items; only 219 of those actually had a
  `DateCreated` in that window. The other 1,535 (87%) were older items
  Jellyfin had re-saved metadata for - a first real run on this filter
  would have announced roughly 1,500 things the owner already had, which
  is precisely the spam problem this whole bot is about avoiding.
- `minDateCreated` looks like the obvious fix and is silently ignored
  entirely - the same total count comes back with or without it.

So there is no working server-side "only what changed" filter. This bot
instead pages backward from the newest item
(`sortBy=DateCreated&sortOrder=Descending`, stepping `startIndex` by
`PAGE_SIZE`) until an item's own `DateCreated` falls below the cursor, and
decides newness from that real field, not from a filter parameter.
`MAX_ITEMS_PER_POLL` caps how far back one poll will page.

This was chosen over keeping `minDateLastSaved` as a cheap prefilter and
double-checking `DateCreated` on the results: that prefilter's cost is not
bounded by how much is actually new, it is bounded by how much of the
library Jellyfin has re-scanned recently, which can drift back toward the
whole library over time. Paging by `DateCreated` costs requests
proportional to real growth since the last poll and nothing else, and it
needs no second date check against a separately-fetched candidate set.

The boundary is still inclusive, because ties happen - a bulk import can
give several items the exact same `DateCreated`, confirmed live where 10
episodes of the same show landed within a millisecond of each other. Two
things close that gap:

1. Every posted item's id is recorded in `posted_items` and filtered out of
   every future fetch, restart included, independent of the cursor.
2. The cursor itself only ever moves forward.

Together, a crash between sending a batch and recording it just repeats
that one fetch on the next cycle, rebuilds the identical group, and sends
under the same deterministic message id (derived from the sorted item ids
in the group) - the same idempotent-retry story `docs/bots/building-bots.md`
describes for a client-generated message id. Verified live against the
running deployment: sending the same message id twice with different
content stored only the first content, exactly as documented.

## What was tested live

Both sides were tested end to end against the real, live deployments, in a
private channel holding only the agent's own account and the bot doing the
posting - deleted, with every test message, afterward.

Against a real Jellyfin 12.0.0 server (`jellyfin.npc-server.top`, 16,506
Movie/Episode items across five libraries): the auth header findings above,
the `DateCreated` paging finding a real 10-episode add (`Cyberpunk:
Edgerunners`, season 1, episodes 1-10) and a real single episode add in the
same window, both posted correctly - the 10-episode add as one batched
message with the series' own poster fetched from Jellyfin and re-hosted as
a slim-m attachment, the single episode as its own card. A cold start
against this same real library correctly seeded on the true newest item
and posted nothing. A second poll over the same unchanged library posted
nothing further, and neither did a third poll after closing and reopening
the sqlite connection to simulate a process restart.

This replaced an earlier round that could not reach a real Jellyfin API
key and was validated only against a stand-in server shaped like
Jellyfin's documented responses - that stand-in could not have shown the
`DateLastSaved` false-positive problem above, since it never modeled a
metadata resave. Worth remembering for anything else built against
Jellyfin from its docs alone: verify field and sort behavior against a
real instance before trusting either.

## What this deliberately does not do

- **Push instead of poll.** Jellyfin has no first-class webhook in core -
  that exists only as a separately-installed plugin, which would mean
  giving Jellyfin a URL back into wherever this bot runs. Every other bot
  in this repo is outbound-only; polling keeps this one the same shape.
- **A cursor per library.** All watched libraries share one cursor, so a
  fast-growing library's timestamps could in theory race ahead of a
  slower one's not-yet-fetched item. `posted_items` is the real backstop
  regardless; this would only change which poll cycle an item appears in,
  never whether it does.
- **Recovering from a dead Jellyfin API key.** Treated the same as slim-m's
  own `401`: this exits rather than polling a revoked key forever.
- **Season-specific posters**, or any image past a series', movie's, or
  album's own primary one.
- **Retrying a failed poster separately from its message.** A poster that
  fails to fetch or upload just means that message posts without an
  attachment - the text still goes out, never blocked on the image.
- **Answering commands, or listening to slim-m's websocket at all.** This
  bot only ever writes to slim-m; it has nothing to react to.
- **Catching up a very long outage in one poll.** `MAX_ITEMS_PER_POLL`
  caps how far back a single poll pages. A backlog bigger than that just
  takes more poll cycles to work through - the cursor still only advances
  as far as what actually got posted - rather than this bot ever silently
  dropping the difference.
