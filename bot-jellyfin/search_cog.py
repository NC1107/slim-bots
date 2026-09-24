"""bot-jellyfin's `!jellyfin search|recent|help` command; an extension, see docs/framework.md."""

import asyncio
from datetime import datetime, timedelta, timezone

from slimbots.limits import ValidationError, require_int, require_len

import jellyfin_core


async def run_search(ctx, query):
    wait_message = jellyfin_core._command_cooldown.check(ctx.author.id)
    if wait_message:
        await ctx.reply(wait_message)
        return
    try:
        query = require_len(query.strip(), max_len=jellyfin_core.MAX_QUERY_LENGTH, field="a search query")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    try:
        items = await asyncio.to_thread(jellyfin_core.search_items, query, jellyfin_core.MAX_SEARCH_RESULTS)
    except jellyfin_core.JellyfinAuthError:
        await ctx.reply("jellyfin search is unavailable right now.")
        return
    if not items:
        await ctx.reply(f'nothing found for "{query}".')
        return
    lines = [
        f"- {item.get('Name')}" + (f" ({item['ProductionYear']})" if item.get("ProductionYear") else "") + f" [{item.get('Type')}]"
        for item in items
    ]
    await ctx.reply("\n".join(lines))


async def run_recent(ctx, days_text):
    wait_message = jellyfin_core._command_cooldown.check(ctx.author.id)
    if wait_message:
        await ctx.reply(wait_message)
        return
    try:
        days = require_int(
            days_text or str(jellyfin_core.RECENT_DEFAULT_DAYS),
            min_value=1, max_value=jellyfin_core.RECENT_MAX_DAYS, field="days",
        )
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
    try:
        items = await asyncio.to_thread(jellyfin_core.items_since, cutoff)
    except jellyfin_core.JellyfinAuthError:
        await ctx.reply("jellyfin is unavailable right now.")
        return
    if not items:
        await ctx.reply(f"nothing added in the last {days} day(s).")
        return
    counts = {}
    for item in items:
        item_type = item.get("Type", "item")
        counts[item_type] = counts.get(item_type, 0) + 1
    summary = ", ".join(f"{count} {item_type}" for item_type, count in sorted(counts.items()))
    await ctx.reply(f"{len(items)} item(s) added in the last {days} day(s): {summary}")


def setup(bot):
    @bot.command(name="jellyfin", help="`search <query>`, `recent [days]`, or `help`", usage="search|recent|help ...")
    async def jellyfin_cmd(ctx, sub: str = "help", rest: str = None):
        sub = sub.lower()
        if sub == "search" and rest:
            await run_search(ctx, rest)
        elif sub == "recent":
            await run_recent(ctx, rest)
        else:
            await ctx.reply(jellyfin_core.HELP_TEXT)
