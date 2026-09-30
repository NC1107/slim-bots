# From "I want a bot" to "it is running"

A bot is an ordinary program that holds a token.
Nothing here installs it for you, and nothing in this repo vouches for it: you read the code, you run it, you decide who gets the token.

## 1. Pick a starting point

Read [`docs/framework.md`](framework.md) once, then copy the closest template.
If none is close, copy [`bots/_template`](../bots/_template/): it builds, runs and passes every gate as it is.

```bash
cp -r bots/_template bots/mybot
```

Keep your own bot in your own repository if it is not meant to be maintained here; the copy does not need to live in this one.

## 2. Make the account and the token

An admin opens Space settings, then Bots, and creates a bot.
The token is shown once and does not rotate, so put it straight into the secret store you will run the bot from.
Treat it as a password: a bot is a member of the Space, and whoever holds the token acts as that member.

A `401` from the server means the token was revoked.
The framework exits on it rather than retrying, which is the behaviour you want.

## 3. Give it the permissions it needs, and no more

Bots may hold any permission, but grant only what the commands use.
A bot that posts needs `VIEW_CHANNEL` and `SEND_MESSAGES` in its channel.
Moderation commands need the matching permission on the bot as well as on the person typing them.
A bot that lacks one should say which, in a reply, instead of failing silently; the templates show how.

## 4. Choose the channel

Set `SLIMM_CHANNELS` to a comma-separated list of channel ids to confine the bot to them.
With one id, `bot.channel` is that channel, which is the usual shape for a bot that posts to one place.
Develop in a private channel that holds only you and the bot.

## 5. Settings

The bot's own settings are environment variables read through `bot.setting(name, default, type=...)`.
`SLIMM_URL` and `SLIMM_BOT_TOKEN` are read by `Bot` itself.
`SLIMM_DB_PATH` moves the sqlite file if your bot keeps state.

## 6. Test before you connect

```bash
cd bots/mybot
PYTHONPATH=../../slimbots python3 test_bot.py
```

The tests use `FakeAsyncClient`, so no server is involved.
Then run the bot against a real Space in the private channel and use each command once, including a bad input.
`scripts/smoke_bot.py` automates that with a manifest under `scripts/smoke/`; see `scripts/smoke/README.md`.

## 7. Run it

Anywhere that can reach your Space over HTTPS and a websocket: a shell, a systemd unit, or a container.
The compose entry only needs the environment and the command:

```yaml
services:
  mybot:
    image: python:3.12-slim
    working_dir: /app
    volumes: ["./bots/mybot:/app", "./data:/data"]
    environment:
      SLIMM_URL: https://your.space
      SLIMM_BOT_TOKEN: ${MYBOT_TOKEN}
      SLIMM_DB_PATH: /data/mybot.db
    command: sh -c "pip install -r requirements.txt && python bot.py"
    restart: unless-stopped
```

Put the token in an `.env` file or your orchestrator's secret store, not in the compose file you commit.

## 8. Contributing it here

Only if you want the project to maintain it as an example.
Follow [`docs/shipping-a-bot.md`](shipping-a-bot.md).
Being in this repo is not an endorsement; see the README.
