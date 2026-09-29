"""bot-seerr's `!request` and `!requests` commands; an extension, see docs/framework.md."""

import asyncio
import urllib.error

from slimbots import ApiError
from slimbots.limits import ValidationError, require_len

import picker
import seerr_core as core

SEERR_ADMIN_BIT = 2
NAME_MAX = 100
RESERVED = ("link", "unlink", "account", "help")


async def _guarded(ctx, call, *args):
    """Runs a blocking Seerr call; on failure answers with a sentence and returns None."""
    try:
        return await asyncio.to_thread(call, *args)
    except core.SeerrAuthError:
        await ctx.reply("seerr rejected the bot's api key - an admin needs to fix it.")
    except (urllib.error.URLError, TimeoutError, OSError):
        await ctx.reply("seerr is unavailable right now (it did not answer).")
    return None


async def _throttled(ctx):
    wait = core._command_cooldown.check(ctx.author.id)
    if wait:
        await ctx.reply(wait)
    return bool(wait)


async def _tell(ctx, text, fallback):
    """Answers privately; a server without private replies gets the name-free `fallback` in the channel."""
    try:
        await ctx.reply_ephemeral(text)
    except ApiError as err:
        if err.status not in (404, 405):
            raise
        await ctx.reply(fallback)


async def seerr_user_for(bot, slimm_user_id):
    """The Seerr user id a member requests as: their link, else the configured default, else None."""
    link = await bot.store.run(core.get_link, slimm_user_id)
    return link[0] if link else core.SEERR_DEFAULT_USER_ID


async def run_request(ctx, title):
    try:
        title = require_len(title.strip(), max_len=core.MAX_QUERY_LENGTH, min_len=1, field="a title")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    if await seerr_user_for(ctx.bot, ctx.author.id) is None:
        await ctx.reply("link your seerr account first: `!request link <your seerr username>`.")
        return
    if await _throttled(ctx):
        return
    found = await _guarded(ctx, core.search, title)
    if found is None:
        return
    open_items = [r for r in found if (r.get("mediaInfo") or {}).get("status") not in core.UNAVAILABLE_FOR_REQUEST]
    if not found:
        await ctx.reply(f'nothing found for "{title}".')
        return
    if not open_items:
        await ctx.reply(f'everything matching "{title}" is already requested or available.')
        return

    async def on_choose(item):
        return await _file_request(ctx.bot, ctx.author.id, item)

    pick = picker.Pick(ctx.author.id, ctx.author.display_name, ctx.channel_id, open_items, title, on_choose, skipped=len(found) - len(open_items))
    await picker.open_pick(ctx.bot, pick, reply_to_id=ctx.message.get("id"))


async def _file_request(bot, slimm_user_id, item):
    user_id = await seerr_user_for(bot, slimm_user_id)
    try:
        await asyncio.to_thread(core.create_request, item, user_id)
    except core.SeerrAuthError:
        return "seerr rejected the bot's api key - an admin needs to fix it."
    except (urllib.error.URLError, TimeoutError, OSError):
        return f"could not request **{core.result_title(item)}**: seerr did not accept it (it may already be requested)."
    return f"requested **{core.result_title(item)}**."


async def run_pending(ctx):
    if await _throttled(ctx):
        return
    found = await _guarded(ctx, core.pending_requests)
    if found is None:
        return
    requests, total = found
    if not requests:
        await ctx.reply("no requests are waiting for approval.")
        return
    lines = []
    for request in requests:
        media = request.get("media") or {}
        info = await asyncio.to_thread(core.media_details, media.get("mediaType"), media.get("tmdbId"))
        year = f" ({info['year']})" if info["year"] else ""
        lines.append(f"- {info['title']}{year} [{'show' if media.get('mediaType') == 'tv' else 'movie'}] - {core.display_name(request.get('requestedBy'))}")
    more = f"\n...and {total - len(requests)} more." if total > len(requests) else ""
    await ctx.reply(f"{total} waiting for approval:\n" + "\n".join(lines) + more)


async def run_link(ctx, name):
    if await _throttled(ctx):
        return
    try:
        name = require_len((name or "").strip(), max_len=NAME_MAX, min_len=1, field="a seerr username")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    user = await _guarded(ctx, core.find_user, name)
    if user is None:
        await _tell(ctx, f'there is no seerr user called "{name}".', "no such seerr user.")
        return
    if user.get("permissions", 0) & SEERR_ADMIN_BIT:
        await _tell(ctx, "admin accounts cannot be linked - their requests would skip approval.", "that account cannot be linked.")
        return
    taken_by = await ctx.bot.store.run(core.owner_of, user["id"])
    if taken_by not in (None, ctx.author.id):
        await _tell(ctx, f'"{core.display_name(user)}" is already linked to someone else.', "that seerr user is already linked.")
        return
    await ctx.bot.store.run(core.set_link, ctx.author.id, user["id"], core.display_name(user))
    await _tell(ctx, f'linked you to the seerr user "{core.display_name(user)}". `!request` now files requests as them, and `!request unlink` undoes it.', "linked.")


async def run_unlink(ctx):
    removed = await ctx.bot.store.run(core.remove_link, ctx.author.id)
    await _tell(ctx, "unlinked." if removed else "you were not linked to a seerr user.", "done.")


async def run_account(ctx):
    link = await ctx.bot.store.run(core.get_link, ctx.author.id)
    text = f'you are linked to the seerr user "{link[1]}".' if link else "you are not linked. `!request link <seerr username>` links you."
    await _tell(ctx, text, "see `!request help`.")


def setup(bot):
    bot.button(prefix=picker.ID_PREFIX)(picker.on_pick_press)

    @bot.command(name="request", help="`<title>`, `link <seerr username>`, `unlink`, `account` or `help`", usage="<title>|link|unlink|account|help")
    async def request_cmd(ctx, sub: str = "help", rest: str = None):
        word = sub.lower()
        if word == "link":
            await run_link(ctx, rest)
        elif word == "unlink":
            await run_unlink(ctx)
        elif word == "account":
            await run_account(ctx)
        elif word == "help":
            await ctx.reply(core.HELP_TEXT)
        else:
            await run_request(ctx, sub if rest is None else f"{sub} {rest}")

    @bot.command(name="requests", help="Requests waiting for approval")
    async def requests_cmd(ctx):
        await run_pending(ctx)
