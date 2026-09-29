# starboard

A slim-m bot: when a message collects enough of one reaction, it is reposted to a highlights channel with a link back.
Once a week it posts a digest of the top highlights.

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It uses `on_raw_message`, `on_reactions_changed`, `on_message_edited` and `on_message_deleted`, and keeps its state in its own sqlite file (`SLIMM_DB_PATH`, default `starboard.db`).

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNELS=<source channel ids> \
STARBOARD_CHANNEL=<highlights channel id> \
python3 bot.py
```

It needs `VIEW_CHANNEL` in every source channel, and `VIEW_CHANNEL` plus `SEND_MESSAGES` in the highlights channel.
It needs `MANAGE_MESSAGES` in the highlights channel only if an admin should be able to delete its highlights; it edits and deletes its own posts otherwise.

## Which channels are mirrored

Only the channels in `SLIMM_CHANNELS`.
That list is the privacy boundary: a highlight quotes the original message into `STARBOARD_CHANNEL`, so list only channels whose readers may all see the highlights channel.
Do not put a private channel in `SLIMM_CHANNELS` unless the highlights channel is exactly as private.
The bot cannot check this for you, since a channel's audience is not something a bot can read.
The highlights channel itself is never a source, even if it is listed.

## Settings

- `STARBOARD_CHANNEL` (required) - the channel id highlights and digests are posted to.
- `STARBOARD_EMOJI` (default the star emoji) - the reaction that counts.
- `STARBOARD_THRESHOLD` (default `3`) - how many of that reaction a message needs.
- `STARBOARD_DIGEST_DAYS` (default `7`) - days between digests; `0` turns the digest off.
- `STARBOARD_DIGEST_TOP` (default `5`) - how many highlights a digest lists.
- `STARBOARD_LINK_TEMPLATE` (default `$SLIMM_URL/channels/{channel_id}`) - the link in a highlight; `{channel_id}` and `{message_id}` are filled in.
  Set it when the web client is served under another path (for example behind `/app`).
- `STARBOARD_SEEN_RETENTION_DAYS` (default `14`) - how long a message the bot has seen stays eligible to be starred.

## How it behaves

- A highlight is posted once per original.
  Its id is derived from the original message id, so a crash between posting and saving reposts under the same id and the server drops the duplicate.
- The count in the highlight follows the reaction count, up and down, by editing the highlight in place.
  A highlight is not removed when the count later drops below the threshold.
- Editing the original updates the quote.
  Deleting the original deletes the highlight and forgets it.
- The digest lists the top highlights starred since the last digest, by count.
  The first run only starts the clock, and a week with no highlights posts nothing.

## What this deliberately does not do

- **Mirror a message it never saw live.**
  A `reactions.changed` frame carries the message id and counts only, and there is no fetch-one-message-by-id call yet, so the bot keeps the messages it sees (for `STARBOARD_SEEN_RETENTION_DAYS`) and can only star those.
  A message posted while the bot was down is skipped, and the log says so.
- **Ignore the author's own reaction.**
  The frame carries aggregate counts, never who reacted.
- **Link to the message itself.**
  The client only routes to a channel, so the link opens the channel and the quote is what identifies the message.
- **Carry attachments or embeds.**
  A highlight quotes the text and notes how many attachments the original had.
- **List new pins in the digest.**
  It is top highlights only.
