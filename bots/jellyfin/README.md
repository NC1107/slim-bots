# jellyfin

A slim-m bot that watches a Jellyfin server and posts to a channel when
something new is added (a movie, a batch of episodes, an album), answers
`!jellyfin search <query>` / `!jellyfin recent [days]` / `!jellyfin help`,
and can join a voice channel to run a watch party with `!watch <title>`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
JELLYFIN_URL=https://your.jellyfin \
JELLYFIN_API_KEY=... \
python3 bot.py
```

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
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

## Watch party

`!watch <title>` can be typed in any text channel this bot listens on - it
searches Jellyfin the same way `!jellyfin search` does, finds *the
invoker's own* voice channel with `bot.voice.find_member()` (`slimbots`;
see `docs/framework.md`), and joins that one through `bot.voice.join()`
to stream the result in as a screen share. The bot needs `CONNECT` and
`SPEAK` in the invoker's channel, the same grants a human sharing their
screen needs, and says which one is missing if either is absent. Several
matches prompt a numbered pick, answered the same way `ctx.confirm` waits
for a reply. Only one watch party runs per deployment at a time, and its
control commands (`!pause`/`!resume`/`!seek`/`!np`/`!subs`/`!stop`) work
from any channel this bot listens on, not just the one `!watch` was typed
in.

- `!watch <title>` - find the invoker's voice channel, join it, and start
  playing; confirms with "streaming **title** into #channel-name".
- `!pause` / `!resume` - stops or resumes reading the decoded stream;
  ffmpeg blocks on its own full pipe buffer while paused, so it costs no
  CPU and resumes exactly where it left off.
- `!seek <h:mm:ss>` - restarts the transcode at a new position (`mm:ss`
  and a bare second count also work).
- `!np` - an embed with title, position, duration, and subtitle state.
- `!stop` - ends the stream and leaves the call.
- `!subs <lang|off>` - matches a subtitle track by language code or
  display title and restarts the transcode with `SubtitleMethod=Encode`
  burning it in, or clears it with `off`.

`!pause`/`!resume`/`!seek`/`!stop`/`!subs` are refused unless the caller
either started the stream or holds `MANAGE_CHANNELS`. `!watch` itself
refuses with "join a voice channel first, then run `!watch` again" if the
invoker is not in any call this bot can see - never "join this channel's
voice call", since the channel `!watch` was typed in may not have one.

The stream stops itself when the movie ends, or when the voice channel
empties - checked on `on_voice_activity` when the LiveKit webhook (decision
0032) is configured, and on a 20-second roster poll regardless, so this
works even on a deployment that has not wired up the webhook.

Video is Jellyfin's own server-side transcode
(`/Videos/{id}/stream?VideoCodec=h264&AudioCodec=aac...`), decoded by a
local `ffmpeg` (a system binary - not pip-installed, and not in the
hash-locked CI requirements since the test suite never spawns it) into
raw I420 frames letterboxed to `JELLYFIN_STREAM_WIDTH`x`JELLYFIN_STREAM_HEIGHT`
and PCM audio, published through `bot.voice`'s `SOURCE_SCREENSHARE`/
`SOURCE_SCREENSHARE_AUDIO` tracks - see `stream_session.py`.

| Variable | Default | What it does |
| --- | --- | --- |
| `JELLYFIN_STREAM_WIDTH` | `1280` | The published video width; Jellyfin's own aspect ratio is letterboxed into this. |
| `JELLYFIN_STREAM_HEIGHT` | `720` | The published video height. |
| `JELLYFIN_STREAM_FPS` | `30` | The published frame rate. |
| `JELLYFIN_STREAM_MAX_BITRATE` | `8000000` | The `VideoBitrate` Jellyfin is asked to transcode at, in bits/second. |

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
`../../docs/framework.md`'s embeds section for the fallback an older server
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

## What was verified for the watch party

Run for real against `slim-m`'s `scripts/e2e.sh` stack (real LiveKit, real
server, two real headless-Chrome web clients) and a real local Jellyfin
12.1.0 server with a two-track (H.264 + AAC) test file:

- `!watch`, `!pause`, `!resume`, `!seek`, `!np`, `!subs off`, and `!stop`
  all worked end to end against the real REST API and were checked at the
  LiveKit SFU (`ListParticipants`), not just by trusting the bot's own
  replies: the bot's identity showed `ACTIVE` with unmuted `SCREEN_SHARE`
  and `SCREEN_SHARE_AUDIO` tracks for the whole session.
- Both web clients rendered the actual decoded video on their call stage
  under "Jellyfin's screen" (screenshotted), and the SFU-level track
  state confirms the paired audio track was live and unmuted throughout.
- Auto-stop fired correctly both ways: naturally at end of video, and via
  `!stop` with the starter/`MANAGE_CHANNELS` gate (a third member without
  either was refused with the expected reply).
- A live Jellyfin 12.1.0 server's progressive `stream` endpoint silently
  drops the audio track when `Container=ts` is requested even though the
  source has an audio stream (`mp4`/`mkv` do not have this problem) -
  `build_stream_url` uses `mkv` because of this; see its own comment.
- One client (of two already in the call) did not pick up the bot's very
  first join - it saw the bot on a second `!watch` a few minutes later
  with no code change. This reads as a LiveKit-room-event timing edge
  case (a client's own participant-connected callback, not anything this
  bot controls) rather than a bug in `stream_session.py`/`watch_cog.py`,
  but it was not root-caused - see the PR description.
- CPU on the box this ran on (see the PR description for the exact
  hardware): `JELLYFIN_STREAM_WIDTH`/`HEIGHT` at the 1280x720 default drew
  roughly 20% of one core in the bot process (LiveKit's own encode) plus
  11% in the decoding `ffmpeg`; at 1920x1080 that was roughly 87% plus 25%
  - the LiveKit-side software encode, not the Jellyfin transcode or the
  decoding `ffmpeg`, is what 1080p actually costs.
- Not exercised live: a title with multiple search matches (the numbered
  picker), `!subs <lang>` actually burning in a subtitle track (the test
  file carried no subtitle stream), and a deployment where the invoker is
  in a *different* voice channel than the one named (refused by code
  inspection and the unit tests, not by a live attempt).

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
- **More than one watch party per deployment at a time.** `!watch` refuses
  while `watch_cog`'s module-level session is still active; running two
  bot-jellyfin processes against the same deployment was never a goal.
- **Changing the published resolution or frame rate mid-stream.** A
  `!watch` after a `!stop` is how to switch `JELLYFIN_STREAM_WIDTH` et al.,
  which are read once at bot startup.
