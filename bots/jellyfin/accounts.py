"""Which Jellyfin account a slim-m member watches as: `!jellyfin link|unlink|account`; see README.md.
With no link a member uses the shared account (`JELLYFIN_USER_ID`, else the first enabled user)."""

from __future__ import annotations

import asyncio
import contextlib
import time

from slimbots import ApiError
from slimbots.limits import ValidationError, require_len

import jellyfin_core
import playback_progress

MAX_NAME_LENGTH = 100


def get_link(conn, slimm_user_id):
    """`(jellyfin_user_id, jellyfin_name)` for a member, or None."""
    row = conn.execute("SELECT jellyfin_user_id, jellyfin_name FROM user_links WHERE slimm_user_id = ?", (slimm_user_id,)).fetchone()
    return (row[0], row[1]) if row else None


def owner_of(conn, jellyfin_user_id):
    row = conn.execute("SELECT slimm_user_id FROM user_links WHERE jellyfin_user_id = ?", (jellyfin_user_id,)).fetchone()
    return row[0] if row else None


def set_link(conn, slimm_user_id, jellyfin_user_id, jellyfin_name):
    conn.execute(
        "INSERT INTO user_links (slimm_user_id, jellyfin_user_id, jellyfin_name, linked_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(slimm_user_id) DO UPDATE SET jellyfin_user_id = excluded.jellyfin_user_id, "
        "jellyfin_name = excluded.jellyfin_name, linked_at = excluded.linked_at",
        (slimm_user_id, jellyfin_user_id, jellyfin_name, int(time.time())),
    )
    conn.commit()


def remove_link(conn, slimm_user_id):
    removed = conn.execute("DELETE FROM user_links WHERE slimm_user_id = ?", (slimm_user_id,)).rowcount
    conn.commit()
    return removed > 0


def find_user(name):
    """The enabled Jellyfin account called `name` (case-insensitive), from the admin key's `GET /Users`; else None."""
    wanted = name.strip().lower()
    for user in jellyfin_core.jf_get("/Users"):
        if (user.get("Name") or "").lower() == wanted and not (user.get("Policy") or {}).get("IsDisabled"):
            return user
    return None


async def user_for(bot, slimm_user_id):
    """The Jellyfin user id to read and write for this member: their link, else the shared account."""
    link = await bot.store.run(get_link, slimm_user_id) if slimm_user_id else None
    if link is not None:
        return link[0]
    return await asyncio.to_thread(playback_progress.resolve_user_id)


async def _tell(ctx, text, fallback):
    """Answers privately; a server without private replies gets the name-free `fallback` in the channel."""
    try:
        await ctx.reply_ephemeral(text)
    except ApiError as err:
        if err.status not in (404, 405):
            raise
        await ctx.reply(fallback)


async def run_link(ctx, name):
    wait_message = jellyfin_core._command_cooldown.check(ctx.author.id)
    if wait_message:
        await ctx.reply(wait_message)
        return
    try:
        name = require_len((name or "").strip(), max_len=MAX_NAME_LENGTH, field="a jellyfin username")
    except ValidationError as err:
        await ctx.reply(str(err))
        return
    try:
        user = await asyncio.to_thread(find_user, name)
    except jellyfin_core.JellyfinAuthError:
        await ctx.reply("jellyfin is unavailable right now.")
        return
    if user is None:
        await _tell(ctx, f'there is no enabled jellyfin user called "{name}".', "no such jellyfin user.")
        return
    taken_by = await ctx.bot.store.run(owner_of, user["Id"])
    if taken_by not in (None, ctx.author.id):
        await _tell(ctx, f'"{user["Name"]}" is already linked to someone else.', "that jellyfin user is already linked.")
        return
    await ctx.bot.store.run(set_link, ctx.author.id, user["Id"], user["Name"])
    p = ctx.bot.prefix
    await _tell(
        ctx, f'linked you to the jellyfin user "{user["Name"]}". `{p}watch` now uses their resume position and continue watching, and `{p}jellyfin unlink` undoes it.',
        "linked.",
    )


async def run_unlink(ctx):
    removed = await ctx.bot.store.run(remove_link, ctx.author.id)
    text = f"unlinked - `{ctx.bot.prefix}watch` uses the shared jellyfin account again." if removed else "you were not linked to a jellyfin user."
    await _tell(ctx, text, "done.")


async def run_account(ctx):
    link = await ctx.bot.store.run(get_link, ctx.author.id)
    if link is None:
        p = ctx.bot.prefix
        text = f"you are not linked, so `{p}watch` uses the shared jellyfin account. `{p}jellyfin link <jellyfin username>` links you."
    else:
        text = f'you are linked to the jellyfin user "{link[1]}".'
    await _tell(ctx, text, f"see `{ctx.bot.prefix}jellyfin help`.")
