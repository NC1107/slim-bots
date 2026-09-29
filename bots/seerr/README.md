# seerr

A slim-m bot for a Seerr (Jellyseerr) instance: it announces new, approved, declined and available requests, lets members request titles with buttons, and lets trusted members approve or decline from the channel.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<channel-uuid> \
SEERR_URL=http://jellyseerr:5055 \
SEERR_API_KEY=... \
python3 bot.py
```

Shared code lives in `../arrkit`; a copy of this bot needs it alongside.
Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
`SLIMM_CHANNELS` names exactly one channel: announcements land there and the commands are answered there.

## Commands

- `!request <title>` searches movies and shows, leaves out what is already requested or available, and opens a button chooser.
  Only the member who ran the command can press its buttons, and the chooser expires after five minutes.
  A show is requested with all seasons.
- `!requests` lists the requests waiting for approval.
- `!request link <seerr username>`, `!request unlink` and `!request account` manage which Seerr user a member requests as.

A request is filed as the member's linked Seerr user, so Seerr's own quotas and auto-approve rules apply to them.
With no link the bot asks the member to link first, unless `SEERR_DEFAULT_USER_ID` names a Seerr user to use instead.
Admin Seerr accounts cannot be linked, because their requests would skip approval, and one Seerr user links to at most one member.

## Approve and decline

A new pending request is posted with Approve and Decline buttons.
Pressing one needs the slim-m permission named by `SEERR_APPROVER_PERMISSION` (default `MANAGE_SERVER`; administrators pass every check).
The check reads the presser's roles, which needs the bot itself to hold `MANAGE_ROLES`: without it the bot says so instead of guessing, and nobody can approve from the channel.
The decision is made with the bot's Seerr key, so Seerr records the bot's account as the decider and the post names the member who pressed.

## Announcements

The bot polls the most recently modified requests every `SEERR_POLL_SECONDS` (default 60).
A cold start marks everything already there as announced, so the backlog is never posted.

Repeats are dropped by a key per state, not per poll:

- **Requested** once per request, and **Approved** or **Declined** once per request.
  A request first seen already decided is one post.
  A decision made with the buttons is not announced a second time.
- **Available** and **Partly available** once per title, listing every requester of it, however many requests there were.

A poll where a send fails retries next cycle; what already landed is remembered.

## Configuration

| Variable | Meaning |
| --- | --- |
| `SEERR_URL` | Seerr's base URL. Plaintext is accepted only for loopback or a bare docker service name; anything else must be https. |
| `SEERR_API_KEY` | The key from Settings, General. It goes in an `X-Api-Key` header and is never logged. |
| `SEERR_POLL_SECONDS` | Poll interval, default 60. |
| `SEERR_APPROVER_PERMISSION` | The slim-m permission that may approve and decline, default `MANAGE_SERVER`. |
| `SEERR_DEFAULT_USER_ID` | Optional Seerr user id to request as for members who have not linked. |
