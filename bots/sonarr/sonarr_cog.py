"""bot-sonarr's `!sonarr search|add|queue|calendar|help` command; an extension, see docs/framework.md."""

import asyncio
import urllib.error

from slimbots.limits import ValidationError, require_int, require_len

import picker
import sonarr_core as core


def label(item):
    year = f" ({item['year']})" if item.get("year") else ""
    return f"{item.get('title') or 'Untitled'}{year}"


async def _guarded(ctx, call, *args):
    """Runs a blocking Sonarr call; on failure answers with a sentence and returns None."""
    try:
        return await asyncio.to_thread(call, *args)
    except core.SonarrAuthError:
        await ctx.reply("sonarr rejected the bot's api key - an admin needs to fix it.")
    except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as err:
        detail = str(err) if isinstance(err, RuntimeError) else "it did not answer"
        await ctx.reply(f"sonarr is unavailable right now ({detail}).")
    return None


async def _throttled(ctx):
    wait = core._command_cooldown.check(ctx.author.id)
    if wait:
        await ctx.reply(wait)
    return bool(wait)


async def _lookup(ctx, query):
    """Validates, then spends the cooldown, then asks Sonarr; bad input costs nothing."""
    try:
        query = require_len((query or "").strip(), max_len=core.MAX_QUERY_LENGTH, min_len=1, field="a show name")
    except ValidationError as err:
        await ctx.reply(str(err))
        return None, None
    if await _throttled(ctx):
        return None, None
    found = await _guarded(ctx, core.search_series, query)
    if found is None:
        return None, None
    if not found:
        await ctx.reply(f'nothing found for "{query}".')
        return None, None
    return query, found[: core.MAX_RESULTS]


async def run_search(ctx, query):
    query, found = await _lookup(ctx, query)
    if found is None:
        return
    lines = [f"- {label(s)}" + (" - in the library" if s.get("id") else "") for s in found]
    await ctx.reply(f'{len(found)} match(es) for "{query}":\n' + "\n".join(lines))


async def run_add(ctx, query):
    query, found = await _lookup(ctx, query)
    if found is None:
        return
    addable = [s for s in found if not s.get("id")]
    if not addable:
        await ctx.reply(f'everything matching "{query}" is already in the library.')
        return

    pick = picker.Pick(ctx.author.id, ctx.author.display_name, ctx.channel_id, addable, query, _guarded_add)
    await picker.open_pick(ctx.bot, pick, reply_to_id=ctx.message.get("id"))


async def _guarded_add(series):
    try:
        await asyncio.to_thread(core.add_series, series)
    except core.SonarrAuthError:
        return "sonarr rejected the bot's api key - an admin needs to fix it."
    except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as err:
        reason = str(err) if isinstance(err, RuntimeError) else "sonarr did not accept it"
        return f"could not add **{label(series)}**: {reason}."
    return f"added **{label(series)}** - searching for episodes now."


async def run_queue(ctx):
    if await _throttled(ctx):
        return
    data = await _guarded(ctx, core.api, "GET", "/queue", {"page": 1, "pageSize": core.QUEUE_LIMIT, "includeSeries": "true", "includeEpisode": "true"})
    if data is None:
        return
    records = data.get("records", [])
    if not records:
        await ctx.reply("the download queue is empty.")
        return
    lines = []
    for r in records:
        ep, series = r.get("episode") or {}, r.get("series") or {}
        name = f"{series.get('title', r.get('title', 'unknown'))} {core.episode_label(ep.get('seasonNumber', 0), ep.get('episodeNumber', 0))}" if ep else r.get("title", "unknown")
        left = f", {r['timeleft']} left" if r.get("timeleft") else ""
        lines.append(f"- {name} - {r.get('status', 'unknown')}{left}")
    total = data.get("totalRecords", len(records))
    more = f"\n...and {total - len(records)} more." if total > len(records) else ""
    await ctx.reply(f"{total} in the queue:\n" + "\n".join(lines) + more)


async def run_calendar(ctx, days_text):
    if await _throttled(ctx):
        return
    try:
        days = require_int(days_text or str(core.CALENDAR_DEFAULT_DAYS), min_value=1, max_value=core.CALENDAR_MAX_DAYS, field="days")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    start, end = core.calendar_window(days)
    items = await _guarded(ctx, core.api, "GET", "/calendar", {"start": start, "end": end, "includeSeries": "true"})
    if items is None:
        return
    if not items:
        await ctx.reply(f"nothing airs in the next {days} day(s).")
        return
    items = sorted(items, key=lambda i: i.get("airDateUtc") or "")
    lines = [
        f"- {(i.get('airDateUtc') or '')[:10]} {(i.get('series') or {}).get('title', 'unknown')} "
        f"{core.episode_label(i.get('seasonNumber', 0), i.get('episodeNumber', 0))}" + (" - downloaded" if i.get("hasFile") else "")
        for i in items[:25]
    ]
    more = f"\n...and {len(items) - 25} more." if len(items) > 25 else ""
    await ctx.reply(f"{len(items)} episode(s) in the next {days} day(s):\n" + "\n".join(lines) + more)


def setup(bot):
    bot.button(prefix=picker.ID_PREFIX)(picker.on_pick_press)

    @bot.command(name="sonarr", help="`search`, `add`, `queue`, `calendar` or `help`", usage="search|add|queue|calendar|help ...")
    async def sonarr_cmd(ctx, sub: str = "help", rest: str = None):
        sub = sub.lower()
        if sub == "search":
            await run_search(ctx, rest)
        elif sub == "add":
            await run_add(ctx, rest)
        elif sub == "queue":
            await run_queue(ctx)
        elif sub == "calendar":
            await run_calendar(ctx, rest)
        else:
            await ctx.reply(core.HELP_TEXT)
