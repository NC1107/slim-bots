# github-releases

A slim-m bot that posts each new GitHub release of a project, with its notes condensed to a short plain list, into one channel.
Pointed at `Slim-m-org/slim-m` it announces client and server releases without anyone leaving the app.

Needs `slim-m>=0.8.0` (bool and list settings).
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It keeps its state in its own sqlite file (`SLIMM_DB_PATH`, default `github-releases.db`).

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
python3 bot.py
```

`SLIMM_CHANNELS` names exactly one channel, and the bot needs `VIEW_CHANNEL` and `SEND_MESSAGES` in it.
It reads no messages and has no commands.

## What it posts

One message per release, oldest first:

```text
**client 0.93.0**
https://github.com/Slim-m-org/slim-m/releases/tag/client-v0.93.0

- hold to lift in the desktop rail, a slimm link that reaches the running linux app, and left anchored poll bars
- a lone channel can be dragged into a category
- the phone channel list drops the grips and kebabs and tightens its spacing
- seven phone ui fixes from the backlog, one device row per install, and a tidier call screen
```

The title is the tag minus its prefix, with the product taken from the prefix.
Notes are the release body reduced to one line per change: headings, commit hashes, PR links and `**scope:**` markup are dropped.
At most 8 changes are listed, then `and N more`, and the whole message stays under 1500 characters.
A release with no notes is just the title and link.

## When it posts

- The first run records every release that exists and posts nothing, so a new deployment does not spam.
  Set `GH_ANNOUNCE_LATEST=1` to post only the newest matching release that first time.
- A restart never reposts: posted release ids live in `SLIMM_DB_PATH`, and a send keeps a stable message id as well.
- Drafts and prereleases are skipped, and posted if they later become a normal release.
- Only tags starting with a prefix in `GH_TAG_PREFIXES` post, so `schema-v` releases stay quiet by default.
  Other tags are recorded as seen, so adding a prefix later does not post old history.
- A failed send is retried next cycle and nothing already posted is repeated.

## Talking to GitHub

The bot lists the repo's 30 newest releases every `GH_POLL_SECONDS` and sends the previous `ETag`, so an unchanged list is a cheap `304`.
The ETag moves only after every post for that list landed.
A rate limit (`429`, or `403` with none remaining) waits for `Retry-After` or the reset time, capped at an hour, and logs one short line.
GitHub being down or answering something that is not a release list logs one line and tries again next cycle.
Unauthenticated, GitHub allows 60 requests an hour per address, which a 10 minute poll stays well under.

## Configuration

| Variable | Meaning |
| --- | --- |
| `SLIMM_CHANNELS` | The one channel releases are posted to. |
| `GH_REPO` | `owner/name` to watch, default `Slim-m-org/slim-m`. |
| `GH_TAG_PREFIXES` | Comma-separated tag prefixes to post, default `client-v,server-v`. |
| `GH_POLL_SECONDS` | Poll interval, default 600, never below 60. |
| `GH_ANNOUNCE_LATEST` | `1` to post the newest matching release on the very first run, default off. |
| `GITHUB_TOKEN` | Optional, only raises the rate limit. It goes in an `Authorization` header and is never logged or posted. A token with no scopes is enough for a public repo. |

## Running it in docker compose

```yaml
  github-releases:
    build: ./slim-bots
    command: python bots/github-releases/bot.py
    restart: unless-stopped
    environment:
      SLIMM_URL: https://your.space
      SLIMM_BOT_TOKEN: ${GITHUB_RELEASES_BOT_TOKEN}
      SLIMM_CHANNELS: ${GITHUB_RELEASES_CHANNEL}
      GITHUB_TOKEN: ${GITHUB_RELEASES_GH_TOKEN:-}
    volumes:
      - github-releases-data:/data
```

Match the `build`, `command` and image pin to how your other bots are run, and keep `SLIMM_DB_PATH` (`/data/github-releases.db`) on a volume, or a restart forgets what it posted and treats the next run as a first run.

## What it does not do

- It does not read release assets, only the body and link.
- It does not edit a message when a release is edited afterwards, and it does not delete one when a release is deleted.
- It only sees the 30 newest releases, so more than 30 releases published between polls would miss the oldest.
- Release notes are posted as the maintainers wrote them, so point it only at a repo whose releases you trust.
