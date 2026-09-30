# minecraft

A slim-m bot that bridges one channel and a Minecraft server's chat.
Game chat, joins, leaves, deaths and advancements show up in the channel, and what members type in the channel shows up in game as `[slim] name: text`.
`!online` lists who is on.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
MC_LOG_PATH=/minecraft/logs/latest.log \
MC_RCON_HOST=minecraft \
MC_RCON_PASSWORD=... \
python3 bot.py
```

Needs `slim-m>=0.8.0` (see `requirements.txt`).
The bot needs `VIEW_CHANNEL` and `SEND_MESSAGES` in the channel.
`SLIMM_CHANNELS` should name exactly one channel, the bridge channel, or set `MC_CHANNEL` explicitly.

## How it reads the server, and why

There are three realistic ways in: tail the server log, RCON, or a mod or plugin on the server.
This bot uses the log for everything coming out of the game and RCON for everything going in.

- **Log tail** gets chat, joins, leaves, deaths and advancements.
  It only needs read access to `latest.log`, so it works on vanilla, Paper, Forge and Fabric, and on modpacks, without installing anything in the server.
  The line format is nearly the same everywhere: Paper and Spigot drop the thread (`[12:00:01 INFO]:`), and modded servers add a logger name after the level, which the parser allows for.
- **RCON** sends `tellraw` into the game and answers `!online` with the server's own `list`.
  It is a vanilla feature (`enable-rcon` in `server.properties`), so again no mod.
- **A mod or plugin** would give cleaner events, but it means one per loader and per modpack, kept up to date by whoever runs the server.
  That is the thing this bot avoids, so it is left out on purpose.

The cost is that the bot is only as good as what the server writes to its log, see the limits below.
Both halves are optional in a sense: with no RCON settings the bridge is one way (game to channel), and `!online` is answered from the log.

## What the owner must supply

| Variable | Default | Meaning |
| --- | --- | --- |
| `SLIMM_URL`, `SLIMM_BOT_TOKEN`, `SLIMM_CHANNELS` | required | The usual bot settings. |
| `MC_LOG_PATH` | required | Path to the server's `latest.log` as the bot sees it. Needs to be a file the bot can read, usually a read-only bind mount. |
| `MC_RCON_HOST` | none | Host of the server's RCON port. With it unset the bridge is one way. |
| `MC_RCON_PORT` | `25575` | The server's `rcon.port`. |
| `MC_RCON_PASSWORD` | none | The server's `rcon.password`. Required when `MC_RCON_HOST` is set. |
| `MC_CHANNEL` | the one `SLIMM_CHANNELS` entry | The bridge channel. |
| `MC_ANNOUNCE` | `chat,join,leave,death,advancement` | Which game events are posted. Chat, joins etc are all tracked either way. |
| `MC_BATCH_SECONDS` | `3` | Minimum seconds between posts, never below 1. Lines collected in that time go out as one message. |
| `MC_MAX_LINES_PER_POST` | `20` | Lines joined into one post. |
| `MC_MAX_POSTS_PER_MINUTE` | `10` | Hard cap on posts to the channel. |
| `MC_QUEUE_MAX` | `200` | Lines held while waiting. Past it the oldest are dropped, and the next post says how many. |
| `MC_TO_GAME_PER_MINUTE` | `30` | Channel messages relayed into the game per minute. Extra ones are dropped, quietly. |

On the server side RCON has to be turned on in `server.properties` (`enable-rcon=true`, `rcon.port`, `rcon.password`), and the server has to be restarted for that to apply.
RCON is plain TCP and the password crosses it unencrypted, so use a docker network or a LAN address, never a public port.

### On a Pelican panel

Pelican keeps each server's files on the node, usually under `/var/lib/pelican/volumes/<server-uuid>/`, so `MC_LOG_PATH` would be `<that>/logs/latest.log` bind mounted into the bot's container.
I haven't checked that path against the owner's node, so it's worth confirming.
RCON needs the server's RCON port to be an allocation the bot can reach from where it runs.
The bot does not read Pelican's API, so it shares nothing with the `pelican` bot and there is no second set of panel credentials.

## Safety

- **No impersonation in either direction.**
  Game to channel: the player name is only accepted if it is a valid Minecraft name, goes in its own code span, and the message text goes in another.
  Death text, which can carry a player-chosen mob name, is in a code span too.
  Backticks are removed from text, newlines and colour codes are stripped, and an `@` gets a zero width character after it so `@everyone` from in game cannot ping anyone.
  Channel to game: the name comes from the member's account, not from the text, and the line is always tagged `[slim]` so it reads as relayed.
  The text travels as a JSON string inside `tellraw`, so it cannot carry a selector, a click action or a second command, and it is clipped to 256 characters and to 1400 bytes once escaped, so one command always fits a single RCON packet.
  A log line longer than 8192 bytes is dropped whole, because the tail of a long line (a player can send a very long command) could otherwise be read as a line of its own.
- **No echo.**
  A channel message only goes into the game, and only game lines that look like chat, joins, leaves, deaths or advancements come back.
  Chat that begins with `[slim]` is dropped (so a player who types it by hand is not relayed either, which only ever hides their own message), bots and the bot's own messages are never relayed, and `!commands` are not relayed either.
- **Rate limits.**
  A busy server is batched and capped as described above, and the same goes for the direction into the game.
- **The password** is only sent to `MC_RCON_HOST`, and is never in a log line or an error.

## What it cannot do

- **Deaths need to be in the log as plain text**, and are matched against a list of the vanilla death wordings.
  A modpack that adds its own death messages won't show up until the wording is added to `_DEATH` in `mc_core.py`.
  A death is only posted for a player the bot knows is online, from a join, from chat, or from the RCON `list`, so a death of someone who never spoke and joined before the bot started could be missed until the next roster sync (every 60 seconds with RCON).
- **Chat formats from a chat plugin or mod** that changes `<name> text` (ranks, prefixes, channels) are not recognised and get ignored.
  Only the vanilla form, optionally with `[Not Secure]`, is.
- **No RCON means no channel to game**, and `!online` only knows what the log showed since the bot started.
- **No webhook style name.**
  Posts come from the bot with the player name in the text, since slim-m has no per-post username label yet.
- **Nothing is replayed.**
  The bot starts reading at the end of the log, so chat from while it was down is not posted.
- **Server start, stop and crashes** are not announced.
- **Attachments** from the channel are relayed as "(sent an attachment)", and nothing else about them.
- **Bedrock and proxy servers** are untested.
  A Floodgate style `.name` is accepted as a name, nothing more.

## Tests

```bash
cd bots/minecraft
for t in test_*.py; do python3 "$t"; done
```

`test_bot.py` drives the bot against a fake slim-m client, a real log file and a real local RCON socket, `test_core.py` covers parsing, cleaning, the log tail and the RCON client, and `test_flow.py` covers batching and the rate limits on a fake clock.
