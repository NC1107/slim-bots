"""bot-seerr's `!request` and `!requests` commands; an extension, see docs/framework.md."""

import asyncio

from arrkit.chooser import Chooser, Pick
from arrkit.guard import UNREACHABLE, Guard
from slimbots import ApiError
from slimbots.limits import ValidationError, require_len

import approvals
import seerr_core as core

GUARD = Guard(core.SERVICE)
CHOOSER = Chooser("seerrpick:", "request", lambda item: core.result_title(item)[:80], "request")
NAME_MAX = 100
RESERVED = ("link", "unlink", "account", "help")


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
        await ctx.reply("you are not linked to a seerr user yet - ask a member who can approve requests to link you.")
        return
    if await GUARD.throttled(ctx):
        return
    found = await GUARD.call(ctx, core.search, title)
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

    pick = Pick(ctx.author.id, ctx.author.display_name, ctx.channel_id, open_items, title, on_choose, note=_skipped_note(len(found) - len(open_items)))
    await CHOOSER.open(ctx.bot, pick, reply_to_id=ctx.message.get("id"))


def _skipped_note(count):
    return f" ({count} already requested or available, left out)" if count else ""


async def _file_request(bot, slimm_user_id, item):
    user_id = await seerr_user_for(bot, slimm_user_id)
    try:
        await asyncio.to_thread(core.create_request, item, user_id)
    except core.AuthError:
        return "seerr rejected the bot's api key - an admin needs to fix it."
    except UNREACHABLE:
        return f"could not request **{core.result_title(item)}**: seerr did not accept it (it may already be requested)."
    return f"requested **{core.result_title(item)}**."


async def run_pending(ctx):
    if await GUARD.throttled(ctx):
        return
    found = await GUARD.call(ctx, core.pending_requests)
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


async def _approver_and_target(ctx, rest):
    """The member a link command names and the text after them, or None after saying what was wrong."""
    refusal = await approvals.approver_refusal(ctx.bot, ctx.author.id, "link or unlink members")
    if refusal:
        await _tell(ctx, refusal, "only approvers can do that.")
        return None
    token, _, remainder = (rest or "").strip().partition(" ")
    member = await ctx.bot.space.get_member(token.lstrip("@")) if token else None
    if member is None:
        await ctx.reply(f"name a member first, for example `{ctx.bot.prefix}request link @member <seerr username>`.")
        return None
    return member, remainder.strip()


async def run_link(ctx, rest):
    target = await _approver_and_target(ctx, rest)
    if target is None:
        return
    member, name = target
    try:
        name = require_len(name, max_len=NAME_MAX, min_len=1, field="a seerr username")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    if await GUARD.throttled(ctx):
        return
    user = await GUARD.call(ctx, core.find_user, name)
    if user is None:
        await ctx.reply(f'there is no seerr user called "{name}".')
        return
    taken_by = await ctx.bot.store.run(core.owner_of, user["id"])
    if taken_by not in (None, member.id):
        await ctx.reply(f'"{core.display_name(user)}" is already linked to someone else.')
        return
    await ctx.bot.store.run(core.set_link, member.id, user["id"], core.display_name(user))
    await ctx.reply(f'linked {member.mention()} to the seerr user "{core.display_name(user)}"; their requests are filed as that user.')


async def run_unlink(ctx, rest):
    target = await _approver_and_target(ctx, rest)
    if target is None:
        return
    member, _ = target
    removed = await ctx.bot.store.run(core.remove_link, member.id)
    await ctx.reply(f"unlinked {member.mention()}." if removed else f"{member.mention()} was not linked to a seerr user.")


async def run_account(ctx):
    link = await ctx.bot.store.run(core.get_link, ctx.author.id)
    if link:
        text = f'you are linked to the seerr user "{link[1]}".'
    elif core.SEERR_DEFAULT_USER_ID is not None:
        text = "you are not linked, so your requests are filed as the shared default seerr user."
    else:
        text = "you are not linked. Ask a member who can approve requests to link you."
    await _tell(ctx, text, f"see `{ctx.bot.prefix}request help`.")


def setup(bot):
    CHOOSER.register(bot)

    @bot.command(name="request", help="`<title>`, `link @member <seerr username>`, `unlink @member`, `account` or `help`", usage="<title>|link @member <seerr user>|unlink @member|account|help")
    async def request_cmd(ctx, sub: str = "help", rest: str = None):
        word = sub.lower()
        if word == "link":
            await run_link(ctx, rest)
        elif word == "unlink":
            await run_unlink(ctx, rest)
        elif word == "account":
            await run_account(ctx)
        elif word == "help":
            await ctx.reply(core.help_text(ctx.bot.prefix))
        else:
            await run_request(ctx, sub if rest is None else f"{sub} {rest}")

    @bot.command(name="requests", help="Requests waiting for approval")
    async def requests_cmd(ctx):
        await run_pending(ctx)
