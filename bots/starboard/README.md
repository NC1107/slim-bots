# starboard

A slim-m bot: when a message collects enough of one reaction, it is reposted to a highlights channel with a link back.
Once a week it posts a digest of the top highlights.

Needs `slim-m>=0.9.0` (`AsyncClient.get_message`, `Channel.restricted`), so do not deploy it before 0.9.0 is on PyPI.
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
It needs `ATTACH_FILES` there too if a highlight should carry the original's attachments; without it the highlight is posted as text.
It needs `MANAGE_MESSAGES` in the highlights channel only if an admin should be able to delete its highlights; it edits and deletes its own posts otherwise.

## Which channels are mirrored

Only the channels in `SLIMM_CHANNELS`.
That list is the privacy boundary: a highlight quotes the original message into `STARBOARD_CHANNEL`, so list only channels whose readers may all see the highlights channel.
Do not put a private channel in `SLIMM_CHANNELS` unless the highlights channel is exactly as private.
The bot also refuses one case itself: it never mirrors a channel that may be restricted into a highlights channel that is not.
That check reads only the `restricted` flag on each channel, which says whether `@everyone` can view it.
It is coarse on purpose: two restricted channels always pass, even when their real audiences differ, and a channel whose flag the server did not send counts as possibly restricted.
Per-role and per-member overwrites are not compared, so `SLIMM_CHANNELS` stays the boundary you own.
The highlights channel itself is never a source, even if it is listed.

## Settings

- `STARBOARD_CHANNEL` (required) - the channel id highlights and digests are posted to.
- `STARBOARD_EMOJI` (default the star emoji) - the reaction that counts.
- `STARBOARD_THRESHOLD` (default `3`) - how many of that reaction a message needs.
- `STARBOARD_DIGEST_DAYS` (default `7`) - days between digests; `0` turns the digest off.
- `STARBOARD_DIGEST_TOP` (default `5`) - how many highlights a digest lists.
- `STARBOARD_LINK_TEMPLATE` (default `$SLIMM_URL/channels/{channel_id}/m/{message_id}`) - the link in a highlight; `{channel_id}` and `{message_id}` are filled in.
  Set it when the web client is served under another path (for example behind `/app`).
- `STARBOARD_SEEN_RETENTION_DAYS` (default `14`) - how long a message the bot has seen stays eligible to be starred.

## How it behaves

- A message the bot did not see live (posted while it was down, or older than `STARBOARD_SEEN_RETENTION_DAYS`) is fetched by id when it reaches the threshold.
  If the fetch fails, it is skipped and the log says why.
- The bot's own messages, other bots' messages and webhook messages are never mirrored.
- A highlight is posted once per original.
  Its id is derived from the original message id, so a crash between posting and saving reposts under the same id and the server drops the duplicate.
- The count in the highlight follows the reaction count, up and down, by editing the highlight in place.
  A highlight is not removed when the count later drops below the threshold.
- Editing the original updates the quote.
  Deleting the original deletes the highlight and forgets it.
- The digest lists the top highlights starred since the last digest, by count.
  The first run only starts the clock, and a week with no highlights posts nothing.

## What this deliberately does not do

- **Ignore the author's own reaction.**
  The frame carries aggregate counts, never who reacted.
- **Carry embeds.**
  A highlight quotes the text and carries the original's attachments by their existing id, up to ten.
  Anything left over is noted in the body.
- **List new pins in the digest.**
  It is top highlights only.
