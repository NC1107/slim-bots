# automod

A slim-m bot: rules a moderator writes (message flood, links, listed words, mention spam) that act on a message instead of only logging it.
Every action is posted to a log channel when one is configured, and a timeout can be undone by a person with `!automod lift @member`.

Built on the `slimbots` `Bot` framework - see `../../docs/framework.md`.
It needs `slim-m>=0.6.0`, which added `time_out_member` and `lift_member_timeout`.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
AUTOMOD_FLOOD_MESSAGES=6 \
AUTOMOD_LOG_CHANNEL=general/modlog \
python3 bot.py
```

## Where this sits against the no-scanning decision

slim-m has no automated content or media scanning, so there is no image classification, no toxicity model, nothing that judges what a message means.
This bot does only what a moderator spelled out ahead of time: count messages, count mentions, compare a link's host against a list, match a word against a list.
That is a rule someone wrote and can read, not a scanner.
It is the reason every rule is off until you turn it on.

## Rules

Every rule is off by default.

- **flood**: `AUTOMOD_FLOOD_MESSAGES` (0 = off), `AUTOMOD_FLOOD_SECONDS` (default 10), `AUTOMOD_FLOOD_TIMEOUT` (default 300 seconds).
  More than N messages in M seconds deletes the message that tripped it and times the member out.
- **mention spam**: `AUTOMOD_MENTION_LIMIT` (0 = off), `AUTOMOD_MENTION_TIMEOUT` (default 300).
  More than N `@name` tokens in one message deletes it and times the member out.
- **links**: `AUTOMOD_LINK_POLICY` is `off` (default), `deny` (block listed domains) or `allow` (block every domain not listed), with `AUTOMOD_LINK_DOMAINS` as a comma-separated list.
  A listed domain also covers its subdomains.
  A blocked message is deleted and the member is told why in the channel.
- **new-member links**: `AUTOMOD_NEW_MEMBER_LINK_HOURS` (0 = off).
  Any link from someone this bot saw join in the last N hours is deleted.
- **words**: `AUTOMOD_WORDS` (comma-separated, whole-word, case-insensitive), optionally limited with `AUTOMOD_WORD_CHANNELS` (channel names).
  A hit deletes the message and says so in the channel.

`AUTOMOD_LOG_CHANNEL` is a `category/channel` path or a bare channel name.
With it unset, nothing is logged beyond the bot's own local counts.

Commands: `!automod rules` (what is on), `!automod stats` (how often each rule fired), `!automod lift @member` (needs Kick Members).

## Permissions

The bot needs `MANAGE_MESSAGES` to delete someone else's message and `KICK_MEMBERS` to time a member out (the server's `PUT /members/{id}/timeout` requires it).
The server also refuses a timeout on a member whose permissions the bot's own do not contain, so an admin is never timed out by it.
When either is refused, the log line says which action failed and names the missing permission, rather than pretending it worked.
Anyone holding `MANAGE_MESSAGES` is exempt from every rule, as are other bots.

## What this deliberately does not do

- **Remember across a restart.** Flood counters and join times live in memory.
  A restart forgives everyone, and a member who joined before the bot started is never treated as new.
- **Catch what it was offline for.** It acts on live `message.created` frames only.
  A message posted while it was disconnected is never checked; slim-m does not yet give moderation events a `seq` to catch up from.
- **Edit-evasion.** An edited message is not rechecked.
- **Look at attachments or embeds**, only the message text.
- **Ban or kick.** The strongest action is a timeout, which a human can lift.
