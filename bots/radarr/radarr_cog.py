"""bot-radarr's `!radarr search|add|queue|calendar|help` command; an extension, see docs/framework.md."""

import asyncio
import urllib.error

from slimbots.limits import ValidationError, require_int, require_len

import picker
import radarr_core as core


def label(item):
    year = f" ({item['year']})" if item.get("year") else ""
    return f"{item.get('title') or 'Untitled'}{year}"


async def _guarded(ctx, call, *args):
    """Runs a blocking Radarr call; on failure answers with a sentence and returns None."""
    try:
        return await asyncio.to_thread(call, *args)
    except core.RadarrAuthError:
        await ctx.reply("radarr rejected the bot's api key - an admin needs to fix it.")
    except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as err:
        detail = str(err) if isinstance(err, RuntimeError) else "it did not answer"
        await ctx.reply(f"radarr is unavailable right now ({detail}).")
    return None


async def _throttled(ctx):
    wait = core._command_cooldown.check(ctx.author.id)
    if wait:
        await ctx.reply(wait)
    return bool(wait)


async def _lookup(ctx, query):
    """Validates, then spends the cooldown, then asks Radarr; bad input costs nothing."""
    try:
        query = require_len((query or "").strip(), max_len=core.MAX_QUERY_LENGTH, min_len=1, field="a movie name")
    except ValidationError as err:
        await ctx.reply(str(err))
        return None, None
    if await _throttled(ctx):
        return None, None
    found = await _guarded(ctx, core.search_movies, query)
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


async def _guarded_add(movie):
    try:
        await asyncio.to_thread(core.add_movie, movie)
    except core.RadarrAuthError:
        return "radarr rejected the bot's api key - an admin needs to fix it."
    except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as err:
        reason = str(err) if isinstance(err, RuntimeError) else "radarr did not accept it"
        return f"could not add **{label(movie)}**: {reason}."
    return f"added **{label(movie)}** - searching for a release now."


async def run_queue(ctx):
    if await _throttled(ctx):
        return
    data = await _guarded(ctx, core.api, "GET", "/queue", {"page": 1, "pageSize": core.QUEUE_LIMIT, "includeMovie": "true"})
    if data is None:
        return
    records = data.get("records", [])
    if not records:
        await ctx.reply("the download queue is empty.")
        return
    lines = []
    for r in records:
        left = f", {r['timeleft']} left" if r.get("timeleft") else ""
        lines.append(f"- {label(r.get('movie') or {'title': r.get('title', 'unknown')})} - {r.get('status', 'unknown')}{left}")
    total = data.get("totalRecords", len(records))
    more = f"\n...and {total - len(records)} more." if total > len(records) else ""
    await ctx.reply(f"{total} in the queue:\n" + "\n".join(lines) + more)


def release_in_window(movie, start, end):
    """The release date that put a movie on the calendar: cinemas, digital or physical, whichever falls in the window."""
    dates = [(movie.get(k) or "")[:10] for k in ("inCinemas", "digitalRelease", "physicalRelease")]
    inside = [d for d in dates if d and start <= d <= end]
    return min(inside) if inside else min((d for d in dates if d), default="")


async def run_calendar(ctx, days_text):
    if await _throttled(ctx):
        return
    try:
        days = require_int(days_text or str(core.CALENDAR_DEFAULT_DAYS), min_value=1, max_value=core.CALENDAR_MAX_DAYS, field="days")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    start, end = core.calendar_window(days)
    items = await _guarded(ctx, core.api, "GET", "/calendar", {"start": start, "end": end})
    if items is None:
        return
    if not items:
        await ctx.reply(f"nothing is released in the next {days} day(s).")
        return
    dated = sorted(((release_in_window(m, start, end), m) for m in items), key=lambda pair: pair[0])
    lines = [f"- {when} {label(m)}" + (" - downloaded" if m.get("hasFile") else "") for when, m in dated[:25]]
    more = f"\n...and {len(dated) - 25} more." if len(dated) > 25 else ""
    await ctx.reply(f"{len(items)} release(s) in the next {days} day(s):\n" + "\n".join(lines) + more)


def setup(bot):
    bot.button(prefix=picker.ID_PREFIX)(picker.on_pick_press)

    @bot.command(name="radarr", help="`search`, `add`, `queue`, `calendar` or `help`", usage="search|add|queue|calendar|help ...")
    async def radarr_cmd(ctx, sub: str = "help", rest: str = None):
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
