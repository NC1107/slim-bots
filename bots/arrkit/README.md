# arrkit

The code `bots/sonarr`, `bots/radarr` and `bots/seerr` share.
It is not a bot: there is no `bot.py` here, and it is never run on its own.
Each of those bots puts this directory's parent on `sys.path` and imports `arrkit`, so a bot copied out on its own needs `arrkit` copied with it.

- `service.py` is one outside HTTP/JSON service: `<NAME>_URL`, `<NAME>_API_KEY` and `<NAME>_POLL_SECONDS` through `bot.setting()`, an `X-Api-Key` header, and the plaintext rule.
  Plaintext is accepted only for loopback or a bare docker service name, so the key never crosses a real network unencrypted.
- `store.py` is the sqlite side: a history cursor that only moves forward, and the `announced` dedupe keys.
- `history.py` is Sonarr and Radarr history: paging to what is new, and `plan_events`, which decides what is worth announcing exactly once.
- `poller.py` is the poll loops and the shared entry point: a rejected service key or revoked token ends the process, anything else retries next cycle.
- `library.py` is the `!<name> search|add|queue|calendar|help` command, worded per bot through a `Spec`.
- `chooser.py` is the button chooser: only its invoker may press, it expires after five minutes, and a second press cannot add twice.
- `guard.py` is the per-member cooldown and the sentence a failed service call answers with.
- `testkit.py` is the fakes and harness the bots' tests use.

## How dedupe works

`plan_events` takes a bot-supplied `event_from(record)` that returns an `ident` naming the item, never the file: series, season and episode for sonarr, the tmdb id for radarr.
The rules then hold for both:

- The first import of an ident is **downloaded**.
  A later import at a different quality is **upgraded**, and one at the same quality posts nothing, so a replaced or re-imported file does not repost.
- An import right after the service deleted the old file as an upgrade in the same batch is **upgraded** even when the bot never saw the first one.
- A grab of an ident announced in the last 24 hours is dropped, so a retry does not repeat.
- A failure is announced once per ident and release.

Nothing is recorded until its post lands, and the message id is derived from the keys, so a retry after a failed send neither loses nor repeats a post.
