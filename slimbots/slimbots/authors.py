"""Resolving whether a message's author is automated; see docs/framework.md."""

import sys


class AuthorFilter:
    """Caches each author id's automated-or-not verdict for the process lifetime."""

    def __init__(self, client, *, space=None, ignore_bots=True):
        self._client = client
        self._space = space
        self.ignore_bots = ignore_bots
        self._automated = {}

    async def is_automated(self, user_id):
        if user_id in self._automated:
            return self._automated[user_id]
        cached = self._space.members.get(user_id) if self._space is not None else None
        if cached is not None:
            self._automated[user_id] = cached.is_bot or cached.is_webhook
            return self._automated[user_id]
        try:
            profile = await self._client.get_user(user_id)
        except Exception as err:
            # A failed lookup reads as human: wrongly skipping a bot beats wrongly ignoring a person.
            print(f"author lookup failed for {user_id}: {err}", file=sys.stderr)
            return False
        automated = bool(profile.get("is_bot")) or bool(profile.get("is_webhook"))
        self._automated[user_id] = automated
        return automated
