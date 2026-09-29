# radarr

A slim-m bot that announces what Radarr grabs, imports, upgrades and fails on, and answers `!radarr` commands.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
RADARR_URL=http://radarr:7878 \
RADARR_API_KEY=... \
python3 bot.py
```

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
`SLIMM_CHANNELS` names exactly one channel: announcements land there and `!radarr` is answered there.
The bot needs `VIEW_CHANNEL` and `SEND_MESSAGES` in it.

## Commands

- `!radarr search <movie>` lists matches and marks the ones already in the library.
- `!radarr add <movie>` opens a button chooser of matches not yet in the library, and adds the pressed one monitored, searching for a release.
  Only the member who ran the command can press its buttons, and the chooser expires after five minutes.
- `!radarr queue` lists what is downloading.
- `!radarr calendar [days]` lists upcoming releases in the next few days (default 7, max 30).

Anyone who can type in the channel can add movies, so keep it a private channel.

## Announcements

The bot polls Radarr's history every `RADARR_POLL_SECONDS` (default 60) and posts one embed per movie.
More than five movies of one kind in a single poll (a bulk import) collapse into one summary post.
A cold start begins at the newest history record, so the existing backlog is never announced.
The cursor is the history record id, kept in `SLIMM_DB_PATH`.

Repeats are dropped by the movie's provider id (TMDB, else IMDb), never by file:

- **Downloaded** is posted the first time a movie imports.
- **Upgraded** is posted when a movie imports again at a different quality, or when Radarr deleted its old file as an upgrade in the same poll.
  The same quality again (a re-import or a replaced file) posts nothing.
- **Grabbed** is dropped when the same movie was grabbed in the last 24 hours, so a retry does not repeat.
- **Failed** is posted once per movie and release.

A poll where a send fails keeps its cursor and retries next cycle; what already landed is remembered, so it is not posted twice.

## Configuration

| Variable | Meaning |
| --- | --- |
| `RADARR_URL` | Radarr's base URL. Plaintext is accepted only for loopback or a bare docker service name; anything else must be https. |
| `RADARR_API_KEY` | Radarr's key from Settings, General. It goes in an `X-Api-Key` header and is never logged. |
| `RADARR_POLL_SECONDS` | Poll interval, default 60. |
| `RADARR_QUALITY_PROFILE` | Profile name or id for `add`; default is the first Radarr lists. |
| `RADARR_ROOT_FOLDER` | Root folder path for `add`; default is the first Radarr lists. |
