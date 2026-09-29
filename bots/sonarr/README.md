# sonarr

A slim-m bot that announces what Sonarr grabs, imports, upgrades and fails on, and answers `!sonarr` commands.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
SONARR_URL=http://sonarr:8989 \
SONARR_API_KEY=... \
python3 bot.py
```

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
`SLIMM_CHANNELS` names exactly one channel: announcements land there and `!sonarr` is answered there.
The bot needs `VIEW_CHANNEL` and `SEND_MESSAGES` in it.

## Commands

- `!sonarr search <show>` lists matches and marks the ones already in the library.
- `!sonarr add <show>` opens a button chooser of matches not yet in the library, and adds the pressed one monitored, searching for its episodes.
  Only the member who ran the command can press its buttons, and the chooser expires after five minutes.
- `!sonarr queue` lists what is downloading.
- `!sonarr calendar [days]` lists episodes airing in the next few days (default 7, max 30).

Anyone who can type in the channel can add shows, so keep it a private channel.

## Announcements

The bot polls Sonarr's history every `SONARR_POLL_SECONDS` (default 60) and posts one embed per (kind, series, season): a season pack is one line, not thirty.
A cold start begins at the newest history record, so the existing backlog is never announced.
The cursor is the history record id, kept in `SLIMM_DB_PATH`.

Repeats are dropped by a key of (series, season, episode), never by file:

- **Downloaded** is posted the first time an episode imports.
- **Upgraded** is posted when an episode imports again at a different quality, or when Sonarr deleted its old file as an upgrade in the same poll.
  The same quality again (a re-import or a replaced file) posts nothing.
- **Grabbed** is dropped when the same episode was grabbed in the last 24 hours, so a retry does not repeat.
- **Failed** is posted once per episode and release.

A poll where a send fails keeps its cursor and retries next cycle; what already landed is remembered, so it is not posted twice.

## Configuration

| Variable | Meaning |
| --- | --- |
| `SONARR_URL` | Sonarr's base URL. Plaintext is accepted only for loopback or a bare docker service name; anything else must be https. |
| `SONARR_API_KEY` | Sonarr's key from Settings, General. It goes in an `X-Api-Key` header and is never logged. |
| `SONARR_POLL_SECONDS` | Poll interval, default 60. |
| `SONARR_QUALITY_PROFILE` | Profile name or id for `add`; default is the first Sonarr lists. |
| `SONARR_ROOT_FOLDER` | Root folder path for `add`; default is the first Sonarr lists. |
