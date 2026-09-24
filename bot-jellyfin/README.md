# bot-jellyfin

A slim-m bot that watches a Jellyfin server and posts to a channel when
something new is added (a movie, a batch of episodes, an album), and
answers `!jellyfin search <query>` / `!jellyfin recent [days]` / `!jellyfin help`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
JELLYFIN_URL=https://your.jellyfin \
JELLYFIN_API_KEY=... \
python3 bot.py
```

Built on the `slimbots` `Bot` framework - see `../docs/framework.md`.
`Bot` owns `SLIMM_URL`/`SLIMM_BOT_TOKEN`/`SLIMM_CHANNELS` and the seq
cursor for the `!jellyfin` command surface; every `JELLYFIN_*` variable is
read through `bot.setting()` (two of them, `JELLYFIN_URL`/`JELLYFIN_API_KEY`,
as `required=True`, so a missing one is reported the same clear way as a
missing `SLIMM_URL`) and `bot.data_path` (from `SLIMM_DB_PATH`) is where
its own tables live - this script never imports `os` itself, since the
framework has no opinion on Jellyfin beyond owning that plumbing. The
Jellyfin side is plain HTTP/JSON via `urllib`,
run off the event loop with `asyncio.to_thread` rather than rewritten onto
`httpx`: it is called at most once per `JELLYFIN_POLL_SECONDS` from the poll
loop, and cooldown-guarded from a command, so blocking it briefly costs
nothing worth a second HTTP stack.

**This bot always connects now.** The earlier version only opened a
websocket when a separate `SLIMM_COMMAND_CHANNEL` was set, so a poll-only
deployment never held a connection at all. Porting onto `Bot` means every
ported template shares one connection model instead of some being outbound
-only scripts and some being full bots; `SLIMM_CHANNELS` now does both jobs
- where new-item posts land by default, and where `!jellyfin` is answered.
A deployment that wants poll-only in spirit can simply not grant the bot
`SEND_MESSAGES` anywhere commands would be answered.

`SLIMM_CHANNELS` should name exactly one channel here - `bot.channel` is
that channel. It needs `SEND_MESSAGES` and `ATTACH_FILES` there; see "What
your bot may do" in `docs/bots/building-bots.md` for granting a bot a
channel overwrite.

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

## Commands

- `!jellyfin search <query>` - hits Jellyfin's own search directly, so it
  can answer for something added before this bot ever ran, not just what
  it happened to post.
- `!jellyfin recent [days]` (default 7, max 30) - reuses the same
  `DateCreated` paging the poll loop uses, against a wall-clock cutoff
  instead of the stored cursor.
- `!jellyfin help` (or any unrecognised `!jellyfin ...`) - the two above.

Both real commands share one `cooldown=` on the command itself, and their
input is bounded (`require_len`/`require_int`) before it ever reaches a
Jellyfin request.

## Other settings

| Variable | Default | What it does |
| --- | --- | --- |
| `JELLYFIN_ITEM_TYPES` | `Movie,Episode` | Comma-separated Jellyfin item types to watch. `Audio` is deliberately not in the default - see Batching. |
| `JELLYFIN_LIBRARY_IDS` | unset (whole server) | Comma-separated library (`parentId`) UUIDs to scope to. |
| `JELLYFIN_POLL_SECONDS` | `300` | How often to check Jellyfin for anything new. |
| `JELLYFIN_BATCH_THRESHOLD` | `3` | More than this many ungrouped items of the same type in one poll collapse into one summary message. |
| `JELLYFIN_LIBRARY_ROUTES` | unset | `libraryId:channelId,...` - send one library's posts to its own channel instead of `bot.channel`. A library not listed still falls back to `bot.channel`. |
| `JELLYFIN_EXCLUDE_GENRES` | unset | Comma-separated, case-insensitive. An item carrying any of these genres is dropped before it is ever grouped into a post - still recorded in `posted_items` and still advances the cursor, so it is never re-considered on a later poll. |
| `SLIMM_DB_PATH` | `jellyfin_watch.db` | Where the Jellyfin cursor and dedupe table live. |

## Cold start

The first run posts nothing. It looks up whatever is currently the newest
item in the watched library or libraries, silently records everything that
shares that exact instant as already-seen, and starts watching forward from
there - replaying years of an existing library into a channel the moment
this bot is first pointed at it is exactly the spam problem it exists to
solve, not a feature to preserve.

## Batching

A Jellyfin library scan can add forty episodes, or a whole album, in one
pass. The rule:

- **Episodes group by series.** One new episode posts as that one episode;
  more than one posts as a single message naming the season(s) and episode
  count, with the series' own poster - never a season-specific one.
- **Audio tracks group by album** the same way, which is also why `Audio`
  sits outside `JELLYFIN_ITEM_TYPES`'s default.
- **Everything else posts one message per item** - each with its own
  poster - until more than `JELLYFIN_BATCH_THRESHOLD` land in a single
  poll, at which point they collapse into one summary line with no
  attachment.

## The cursor is `DateCreated`, found by paging, not by filtering

`DateLastSaved` is not a real field - requesting it in `fields` returns
nothing - and `sortBy=DateLastSaved` does not sort by anything date-related.
`minDateLastSaved` is honoured as a filter, but bumps on any metadata
resave, not just on add - tested live against a 16,506-item Jellyfin 12
library, a window meant to mean "added in the last 3 days" returned 1,754
items, of which only 219 actually had a `DateCreated` in that window.
`minDateCreated` is silently ignored entirely.

So this bot pages backward from the newest item
(`sortBy=DateCreated&sortOrder=Descending`, stepping `startIndex` by
`PAGE_SIZE`) until an item's own `DateCreated` falls below the cursor,
capped by `MAX_ITEMS_PER_POLL`. The boundary is inclusive, because ties
happen (a bulk import can give several items the exact same `DateCreated`);
`posted_items` and a cursor that only ever moves forward close that gap -
a crash between sending a batch and recording it just repeats that one
fetch, rebuilds the identical group, and sends under the same deterministic
message id.

## Output

Every post still carries its plain text (title, and a trimmed overview or
episode/album summary) and its poster as a slim-m attachment, unchanged. A
small `Embed` (title and description, no image - the poster stays an
attachment, since an embed's image is a URL the server fetches itself, not
an already-uploaded attachment) now rides alongside it. See
`../docs/framework.md`'s embeds section for the fallback an older server
gets instead.

## What was and was not verified in this port

The auth header, `DateCreated`-vs-`DateLastSaved`, and paging findings above
came from testing an earlier version of this bot live against a real
16,506-item Jellyfin 12 server - that testing is not repeated here, and
this port did not have a working Jellyfin credential available to redo it
against. This port's own testing is `test_bot.py`: grouping, cursor
advance, genre exclusion, library routing, and the command layer (search,
recent, cooldown, bot-ignore) against `FakeAsyncClient` with Jellyfin's own
functions monkeypatched, not a live server. The polling/grouping/auth logic
itself is unchanged from the version that was tested live; only its
integration onto `Bot` (config, the async command loop, the embed) is new
and only test-covered.

## What this deliberately does not do

- **Push instead of poll.** Jellyfin has no first-class webhook in core -
  that exists only as a separately-installed plugin, which would mean
  giving Jellyfin a URL back into wherever this bot runs.
- **A cursor per library.** All watched libraries share one cursor;
  `posted_items` is the real backstop regardless.
- **Recovering from a dead Jellyfin API key.** Treated the same as slim-m's
  own `401`: this exits rather than polling a revoked key forever.
- **Season-specific posters**, or any image past a series', movie's, or
  album's own primary one.
- **Retrying a failed poster separately from its message.** A poster that
  fails to fetch or upload just means that message posts without an
  attachment.
- **Catching up a very long outage in one poll.** `MAX_ITEMS_PER_POLL`
  caps how far back a single poll pages; a bigger backlog just takes more
  cycles, never a silently dropped difference.
