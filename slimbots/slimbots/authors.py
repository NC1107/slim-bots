"""Telling a bot message apart from a human one - the fix for the single
biggest live hazard in this repo.

Discord puts `author.bot` on every message precisely so a bot can skip
another bot's output. slim-m's message frame carries no such flag (see
`docs/bots/building-bots.md`), and every template so far only checked the
author against its own id. With several bots sharing a channel that is a
live loop-and-misfire risk: nothing today stops one bot's own message from
being read as a command by another, and it already happens - a reminder
whose text is `!daily` makes bot-casino credit the *reminders bot's*
account, because casino keys the balance on whoever posted the message.

The fix follows this codebase's own established pattern for exactly this
shape of problem (see the `dont-mask-per-reader-on-the-live-path` note):
send the id, and let the reader resolve it. `GET /users/{id}` already
returns `is_bot` and `is_webhook` for any account (see
`crates/slimm-server/src/http/users.rs`'s `UserDto`), so `AuthorFilter`
resolves it there, once per author id for the life of the process, and
gives every template a default that ignores itself and every other
automated account.
"""

import sys


class AuthorFilter:
    """Classifies message authors as human or automated, caching the
    classification per user id.

    `ignore_bots` (default `True`) is what makes a template skip every bot
    and webhook message, not just its own - opt out for the rare bot that
    genuinely wants to see another bot's output (a moderation-log style bot
    watching everything, say).
    """

    def __init__(self, client, *, ignore_bots=True):
        self._client = client
        self.ignore_bots = ignore_bots
        self._automated = {}

    def is_automated(self, user_id):
        """Whether `user_id` is a bot or a webhook principal, cached after
        the first lookup.

        A failed lookup (a network hiccup, or the account was deleted since
        the message was posted) is never cached - it is retried on the next
        message from the same author - and is treated as human for this one
        call. Wrongly answering a real person because of a transient error
        is worse than occasionally not skipping a bot message.
        """
        cached = self._automated.get(user_id)
        if cached is not None:
            return cached
        try:
            profile = self._client.call("GET", f"/users/{user_id}")
        except Exception as err:
            print(f"author lookup failed for {user_id}: {err}", file=sys.stderr)
            return False
        automated = bool(profile.get("is_bot")) or bool(profile.get("is_webhook"))
        self._automated[user_id] = automated
        return automated

    def should_handle(self, author_id, own_id):
        """Whether an incoming message from `author_id` should be treated as
        input: not us, and - unless `ignore_bots` was turned off - not
        another automated account either.
        """
        if not author_id or author_id == own_id:
            return False
        if self.ignore_bots and self.is_automated(author_id):
            return False
        return True
