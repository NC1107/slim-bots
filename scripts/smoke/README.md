# Smoke manifests

One `<bot>.json` per bot, read by `scripts/smoke_bot.py <bot>`.

```json
{
  "ready_seconds": 8,
  "env": {"SOME_BOT_SETTING": "value"},
  "steps": [
    {"say": "!help", "expect_contains": ["commands"], "timeout": 15}
  ]
}
```

- `say` is typed into the private channel by the tester account.
- `expect_contains` lists substrings the replies must contain; leave it out to only require some reply.
- `env` is passed to the bot process on top of the caller's environment.
- List every documented command of the bot, including one bad-input case per command.

A bot PR adds its own manifest here (not under `bots/<name>/`).
Voice bots need a real LiveKit reachable from where the script runs.
