# bot-webhook-relay

A tiny HTTP server that turns a POSTed JSON body into a slim-m message - the
thing you point Sonarr, Uptime Kuma, Grafana or a CI step at, the way you
would point them at a Discord incoming webhook.

slim-m does not have real incoming webhooks yet. See
[`docs/decisions/0030-incoming-webhooks.md`](https://github.com/NC1107/slim-m/blob/main/docs/decisions/0030-incoming-webhooks.md)
in the slim-m repo, which designs one carefully and has not built it. This
template is the stand-in until that ships: a program you run and point tools
at, not a URL slim-m itself hands out - a bot in the sense
[`docs/bots/building-bots.md`](https://github.com/NC1107/slim-m/blob/main/docs/bots/building-bots.md)
describes, not a webhook in 0030's sense. Read 0030 before building much on
this; the gaps below are close to the exact list it exists to close properly.

```bash
pip install -r requirements.txt
SLIMM_URL=https://your.space \
SLIMM_BOT_TOKEN=slimbot_... \
SLIMM_CHANNEL=<channel-uuid> \
python3 bot.py
```

Then point a tool at `http://<this-machine>:8099/post` (`WEBHOOK_PORT` and
`WEBHOOK_PATH` to move either). A request:

```
POST /post
Content-Type: application/json

{ "content": "backup finished: 4.2 GiB in 3m12s" }
```

answers `204` on success. `username`, if a tool sends one, becomes a label
folded into the text - `**sonarr:** disk at 95%` - never a real identity:
this bot has no principal of its own to post as, so every message stays
authored by `SLIMM_BOT_TOKEN` and badged `Bot`, regardless of what `username`
claims. A real webhook (0030) gets its own principal and its own `Webhook`
badge; this is the honest difference, and the whole reason this is a
stopgap rather than the real thing.

## Idempotency

Send an `Idempotency-Key` header and a retried request with the same key and
body returns the message already stored rather than posting twice. Send
none and every request posts - fine for a one-shot alert, wrong for
anything that retries itself on a timeout. This mirrors decision 0030's own
two-sided rule for the real webhook route: no automatic body-hash dedupe,
because a heartbeat tool legitimately posts a byte-identical body on a
schedule and silently dropping it is the wrong failure mode.

## Securing it

Set `WEBHOOK_SHARED_SECRET` and every request needs a matching
`X-Webhook-Secret` header, checked with `hmac.compare_digest`. Unset,
anyone who can reach the port can post. This is meant for a LAN or behind a
reverse proxy you control, not the open internet - see "What this
deliberately does not do".

## Testing

`test_bot.py` covers `render_content`, `message_id_for`, and the HTTP
handler itself (204/400/401/404, the secret check, the idempotency-key
replay) against `slimbots.testing.FakeClient` - no live deployment, no
socket:

```bash
python3 test_bot.py
```

`FakeClient` ships in `slim-m` 0.2.0, not yet released as of this template;
`pip install "slim-m @ git+https://github.com/NC1107/slim-bots.git@main#subdirectory=slimbots"`
to run the tests until it is. Running `bot.py` itself needs nothing newer
than 0.1.0.

Also tested live: a throwaway private channel, a real deployment, a plain
post, a `username`-labelled post, a wrong-secret 401, and a same-key retry
that landed exactly once. Cleaned up afterward.

## What this deliberately does not do

- **Discord's payload shape.** `content` and `username` only - no `embeds`,
  no `allowed_mentions`, none of the fields decision 0030 spends a section
  on accepting-but-discarding for real tool compatibility. A tool's
  `embeds` are silently dropped here, same as 0030 promises for a field it
  does not honour, but this bot promises far less of the shape overall.
- **Hardened auth.** The shared secret is optional and compared in constant
  time, but there is no per-webhook token, no rotation, and no audit trail -
  0030's 32-byte `generate_secret` token and moderation-audit-logged mint
  and revoke, none of it. One secret, one bot, until you restart it with a
  new one.
- **Rate limiting at the door.** A flood here is bounded only by slim-m's
  own bot rate limit on the send itself; decision 0030's `Class::Webhook`,
  sized against push-amplification cost, has no equivalent. Put this behind
  a reverse proxy if that matters.
- **TLS.** Plain `http.server`. Put it behind Caddy or nginx to leave a LAN.
- **More than one channel per process.** Run a second instance, on a second
  port, for a second channel.
- **Attachments or embeds of any kind.** Text only, 4000 characters, same
  cap `SendMessageRequest.content` already enforces server-side.
