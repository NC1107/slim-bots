## What and why

Card:

## Ship checklist (see docs/shipping-a-bot.md; delete the section for a change that touches no bot)

- [ ] Requirements traced: acceptance criteria from the card listed below, each with its test or transcript line; skipped items named
- [ ] Tests in CI: `test_bot.py` covers it, a regression test fails on the old code, voice changes covered by the real-livekit job, pyright no worse than main
- [ ] In-app test: ran `scripts/smoke_bot.py <bot>` (or by hand) in a private channel with only the tester; transcript or screenshot attached; cleaned up
- [ ] Code review pass done by someone other than the author (link)
- [ ] Adversarial pass done: abuse, permissions, rate limits, secrets (link)
- [ ] UI pass done for member-visible output: desktop and phone width, error replies (screenshot)
- [ ] Rollout: PyPI version and CHANGELOG entry, `slim-m>=` pin in requirements.txt, prod image pin, compose entry

## Permissions

What the bot needs, and why each one (for example `SEND_MESSAGES` in its channel, `MANAGE_ROLES` to hand out a role). Write "none beyond reading and posting" if that is all.

## Acceptance criteria and evidence

## What I could not verify
