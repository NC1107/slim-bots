# Shipping a bot

Every PR that changes something under `bots/<name>/` follows this list, and the PR template repeats it.
It exists because bots shipped green and broken: `rtc.AudioEncoding` called an API the real livekit lacks, `!watch some movie` saw only "some" because of postponed annotations, and an automod PR had conditions that were always false.
A fake or a unit test passing is not evidence that the bot works.

Passes 2 to 5 are done by a reviewer other than the author (a subagent or the owner), and their findings are linked from the PR.
The release itself stays with the main session.

## 1. Requirements traced to the card

- Link the Planka card and copy its acceptance criteria into the PR body, each with the test or transcript line that proves it.
- Check the repo rules: one-line plain comments, short docstrings, no em dash, no emoji, bots are full principals, no bot-specific code in slim-m core.
- Anything the card asked for and the PR skips is listed as skipped, with the reason.

## 2. Tests in CI

- The bot has `test_bot.py` (`bots/ping` is the one exception) and it runs in the `bot-templates` job.
- A regression test fails on the old code before the fix goes in.
- A bot that touches voice is covered by the real-livekit job (`slimbots/tests_livekit`), not only the fake; a new `rtc.<X>` the bot relies on is added to the conformance test.
- `pyright` is no worse than main.

## 3. In-app test against a live deployment

- Run `scripts/smoke_bot.py <bot>` (or drive it by hand) against a real server, in a private channel that holds only the tester and the bot.
- Every documented command has a step in `scripts/smoke/<bot>.json`, including one bad-input case each.
- Attach the transcript file or a screenshot to the PR.
- Clean up after: delete the channel and messages, revoke any token minted for the test.
- Voice bots need a real LiveKit reachable from where the script runs.

## 4. Code review and adversarial pass

Code review: the diff read for correctness, security, performance, and test quality.

Adversarial pass, told to break it, at least:

- **Abuse**: hostile or oversized input, unicode, markdown or mention injection into replies, a command typed by a bot, two invokers at once.
- **Permissions**: the bot lacking a permission it needs (the reply names what is missing), the invoker lacking one the command requires, the command typed in a channel the bot cannot see.
- **Rate limits and cost**: a loop or spammed command cannot flood the channel, the server, or an outside API; cooldowns exist where they should.
- **Secrets**: no token, key, or private URL in a log line, a reply, an error message, or a committed file.

## 5. UI pass (any output members see)

- Look at the replies in the real client at desktop and phone width: formatting, line length, embeds, truncation, command hints, error replies.
- Errors read as a sentence a member can act on, not a traceback or an API status.

## 6. Rollout

- Library change: bump `slimbots/pyproject.toml`, add an entry to `slimbots/CHANGELOG.md`, publish to PyPI.
- Each bot's `requirements.txt` pins `slim-m>=` the release that provides every library feature it uses.
- Prod: bump the image pin so it installs the new release, and add or update the bot's compose entry (each entry hardcodes its `bot.py` path).
- Check the deployed bot answers once after the rollout; a merged change is not a deployed one.

## What CI checks for you

`scripts/check_bot_pr.py --base origin/main` runs in the `bot-checklist` job and fails a PR when a changed bot lacks `test_bot.py`, `README.md` or `requirements.txt`, or pins `slim-m` below the release that provides a library feature its source uses.
It is a heuristic: a table of feature names to first release in the script, matched against the bot's non-test source with comments and strings stripped.
It does not see features missing from the table, calls made through an alias, or a pin that is high enough but wrong for other reasons.
Add a row to the table when the library gains a feature.
Passes 1 and 3 to 6 are not machine-checked; the PR template is the only enforcement, and no CI step calls a model or needs an API key.
