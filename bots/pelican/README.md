# pelican

A slim-m bot that lists and controls game servers on a [Pelican](https://pelican.dev) panel.
It uses the panel's Client API with one API key.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
PELICAN_URL=https://panel.example \
PELICAN_API_KEY=ptlc_... \
PELICAN_LOG_CHANNEL=<channel-uuid> \
python3 bot.py
```

## Commands

- `!servers` - every allowed server with its state, CPU and RAM.
- `!server <name>` - one server in detail: node, disk, uptime, network totals.
  `<name>` is a server name or its short identifier, in any case, or a fragment of one name if that is unambiguous.
- `!start <name>`, `!stop <name>`, `!restart <name>` - power actions, control role only.
  `!stop` and `!restart` ask for a yes/no first.

Every power action posts one line to `PELICAN_LOG_CHANNEL` (who, which server, which signal) before it is sent.
If that line cannot be posted, the action is not sent.
With no log channel configured, the power commands are off.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PELICAN_URL` | required | Panel base URL. Must be `https`. |
| `PELICAN_API_KEY` | required | A Client API key (an account key, `ptlc_...`). |
| `PELICAN_ALLOW_HTTP` | off | `1` allows an `http` URL, for a panel on a trusted LAN only. |
| `PELICAN_SERVERS` | all | Comma-separated server names or identifiers the bot may show and control. |
| `PELICAN_CONTROL_ROLE` | none | A role id whose holders may use the power commands. |
| `PELICAN_CONTROL_PERMISSION` | `MANAGE_SERVER` | A `slimbots` permission name that also grants control. `ADMINISTRATOR` always passes. |
| `PELICAN_LOG_CHANNEL` | none | Channel id for the power-action log. |
| `PELICAN_STATUS_CHANNEL` | none | Channel id for the status message. Unset means no status message. |
| `PELICAN_STATUS_INTERVAL` | `60` | Seconds between status refreshes, never below 30. |

The key is only ever sent as the `Authorization` header to `PELICAN_URL`.
It is not logged, and error replies never contain it.
Redirects from the panel are not followed, so the header cannot be replayed elsewhere.
Use an API key whose account owns or is a subuser of just the servers you want controlled, and keep `PELICAN_SERVERS` as a second fence.

## The status message

The message id is derived from the channel id, so a restart finds and edits the same message instead of posting a new one.
It is edited only when the text changed.
The panel caches resource stats for about 20 seconds, so an interval under that shows the same numbers.

## What it deliberately does not do

- **Player counts.** The Client API does not expose them; they come from the game's own query protocol, which differs per game.
- **Console or `kill`.** No console websocket and no `kill` signal, only start, stop and restart.
- **Announce up/down transitions.** The status message shows the state; it does not post a new message when one changes.
- **Store anything.** No database; the state lives in the panel and the one status message.
