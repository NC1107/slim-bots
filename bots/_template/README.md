# _template

The copy-me starting point: a typed command, a button, a private reply, a permission check and durable state, in one file, with tests that run without a server.

Copy it, rename it, delete what you do not need.
The walk from "I want a bot" to "it is running" is in [`docs/getting-started.md`](../../docs/getting-started.md).

```bash
cp -r bots/_template bots/mybot
cd bots/mybot
pip install -r requirements.txt
PYTHONPATH=../../slimbots python3 test_bot.py
SLIMM_URL=https://your.space SLIMM_BOT_TOKEN=slimbot_... python3 bot.py
```

## What it shows

- `!vote <question>` posts the question with Yes and No buttons. A typed, rest-of-message `str` argument, a per-member cooldown, and a private refusal.
- `@bot.button(prefix="vote:")` counts a press, answers only the presser, and lets them change their vote.
- `!tally <message id>` needs Manage Messages. A member without it gets a private reply, not a channel post.
- `bot.setting("TEMPLATE_MAX_QUESTION", 200, type=int)` reads the bot's own config.
- A sqlite store that survives a restart, through `store_migrate` and `bot.open_store()`.

Each of those is explained in [`docs/framework.md`](../../docs/framework.md).

## What to change first

- Rename the `TEMPLATE_` setting prefix and the `template.db` default to your bot's name.
- Replace the commands, keep the shape: handlers stay short, anything that can fail says so in a sentence, and each command has a test.
- Keep `slim-m>=` at the release that provides every feature you use; `scripts/check_bot_pr.py` fails a PR that pins lower.

## What this deliberately does not do

- **Show voice, menu entries or call controls.** `bots/music` and `bots/jellyfin` cover voice; `@bot.message_menu` and `@bot.call_control` are in `docs/framework.md`.
- **Poll an outside service.** `bots/arrkit` and `bots/pelican` are the worked examples.
- **Stop one member voting on a message twice from two accounts.** It counts a member once, not a person.
- **Close a vote.** A question stays votable for as long as its buttons are on the message.
