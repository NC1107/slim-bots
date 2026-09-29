"""The `!<name> search|add|queue|calendar|help` command Sonarr and Radarr share; each bot supplies its wording."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from slimbots.limits import ValidationError, require_int, require_len

from .chooser import Chooser, Pick
from .guard import UNREACHABLE, Guard
from .service import AuthError

MAX_QUERY_LENGTH = 100
MAX_RESULTS = 8
QUEUE_LIMIT = 10
LIST_LIMIT = 25
CALENDAR_DEFAULT_DAYS = 7
CALENDAR_MAX_DAYS = 30


def label(item):
    year = f" ({item['year']})" if item.get("year") else ""
    return f"{item.get('title') or 'Untitled'}{year}"


@dataclass
class Spec:
    """How one bot words the shared command: its noun, its core's lookup and add, and its list formatters."""

    name: str
    noun: str
    queue_params: dict[str, Any]
    queue_line: Callable[[dict[str, Any]], str]
    calendar_params: dict[str, Any]
    calendar_line: Callable[[dict[str, Any], str, str], str]
    calendar_what: str
    added_text: str


def help_text(spec, prefix):
    return (
        f"commands: `{prefix}{spec.name} search <{spec.noun}>`, `{prefix}{spec.name} add <{spec.noun}>` (pick from buttons), "
        f"`{prefix}{spec.name} queue`, `{prefix}{spec.name} calendar [days]` ({spec.calendar_what}, default {CALENDAR_DEFAULT_DAYS}, max {CALENDAR_MAX_DAYS})."
    )


def calendar_window(days):
    start = datetime.now(timezone.utc)
    return start.strftime("%Y-%m-%d"), (start + timedelta(days=days)).strftime("%Y-%m-%d")


class Library:
    """The command's behavior; `core` is the bot's core module, read late so a test can swap its `api`."""

    def __init__(self, core, spec, guard: Guard, chooser: Chooser):
        self.core = core
        self.spec = spec
        self.guard = guard
        self.chooser = chooser

    async def _lookup(self, ctx, query):
        """Validates, then spends the cooldown, then asks the service; bad input costs nothing."""
        try:
            query = require_len((query or "").strip(), max_len=MAX_QUERY_LENGTH, min_len=1, field=f"a {self.spec.noun} name")
        except ValidationError as err:
            await ctx.reply(str(err))
            return None, None
        if await self.guard.throttled(ctx):
            return None, None
        found = await self.guard.call(ctx, self.core.lookup, query)
        if found is None:
            return None, None
        if not found:
            await ctx.reply(f'nothing found for "{query}".')
            return None, None
        return query, found[:MAX_RESULTS]

    async def search(self, ctx, query):
        query, found = await self._lookup(ctx, query)
        if found is None:
            return
        lines = [f"- {label(s)}" + (" - in the library" if s.get("id") else "") for s in found]
        await ctx.reply(f'{len(found)} match(es) for "{query}":\n' + "\n".join(lines))

    async def add(self, ctx, query):
        query, found = await self._lookup(ctx, query)
        if found is None:
            return
        addable = [s for s in found if not s.get("id")]
        if not addable:
            await ctx.reply(f'everything matching "{query}" is already in the library.')
            return
        pick = Pick(ctx.author.id, ctx.author.display_name, ctx.channel_id, addable, query, self._add_chosen)
        await self.chooser.open(ctx.bot, pick, reply_to_id=ctx.message.get("id"))

    async def _add_chosen(self, item):
        try:
            await asyncio.to_thread(self.core.add, item)
        except AuthError:
            return f"{self.core.SERVICE.name} rejected the bot's api key - an admin needs to fix it."
        except (*UNREACHABLE, RuntimeError) as err:
            reason = str(err) if isinstance(err, RuntimeError) else f"{self.core.SERVICE.name} did not accept it"
            return f"could not add **{label(item)}**: {reason}."
        return f"added **{label(item)}** - {self.spec.added_text}."

    async def queue(self, ctx):
        if await self.guard.throttled(ctx):
            return
        params = {"page": 1, "pageSize": QUEUE_LIMIT, **self.spec.queue_params}
        data = await self.guard.call(ctx, self.core.api, "GET", "/queue", params)
        if data is None:
            return
        records = data.get("records", [])
        if not records:
            await ctx.reply("the download queue is empty.")
            return
        total = data.get("totalRecords", len(records))
        more = f"\n...and {total - len(records)} more." if total > len(records) else ""
        await ctx.reply(f"{total} in the queue:\n" + "\n".join(self.spec.queue_line(r) for r in records) + more)

    async def calendar(self, ctx, days_text):
        try:
            days = require_int(days_text or str(CALENDAR_DEFAULT_DAYS), min_value=1, max_value=CALENDAR_MAX_DAYS, field="days")
        except ValidationError as err:
            await ctx.reply(str(err))
            return
        if await self.guard.throttled(ctx):
            return
        start, end = calendar_window(days)
        items = await self.guard.call(ctx, self.core.api, "GET", "/calendar", {"start": start, "end": end, **self.spec.calendar_params})
        if items is None:
            return
        if not items:
            await ctx.reply(f"nothing in the next {days} day(s).")
            return
        lines = sorted(self.spec.calendar_line(i, start, end) for i in items)
        more = f"\n...and {len(lines) - LIST_LIMIT} more." if len(lines) > LIST_LIMIT else ""
        await ctx.reply(f"{len(items)} item(s) in the next {days} day(s):\n" + "\n".join(lines[:LIST_LIMIT]) + more)

    def setup(self, bot):
        self.chooser.register(bot)
        spec = self.spec

        @bot.command(name=spec.name, help="`search`, `add`, `queue`, `calendar` or `help`", usage="search|add|queue|calendar|help ...")
        async def library_cmd(ctx, sub: str = "help", rest: str = None):
            sub = sub.lower()
            if sub == "search":
                await self.search(ctx, rest)
            elif sub == "add":
                await self.add(ctx, rest)
            elif sub == "queue":
                await self.queue(ctx)
            elif sub == "calendar":
                await self.calendar(ctx, rest)
            else:
                await ctx.reply(help_text(spec, ctx.bot.prefix))
