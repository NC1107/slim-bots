#!/usr/bin/env python3
"""bot-roles: self-service roles - `!role <name>`, `!role remove <name>`, `!role mine`, `!roles`, `!roles status`; see README.md."""

import os
import uuid

from slimbots import Bot, Permissions, catchup
from slimbots.http import ApiError, is_forbidden, is_not_found

BASE = os.environ.get("SLIMM_URL", "")
TOKEN = os.environ.get("SLIMM_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLIMM_CHANNEL", "")

# Namespace for deriving a stable listing-message id from the channel id, so nothing needs to be persisted to disk.
LISTING_NAMESPACE = uuid.UUID("d1f6a9d0-0f0f-4b6a-9b0f-2f6b6f0f9a10")


def parse_roles(spec):
    """`"name:uuid,name:uuid"` -> an order-preserving `{name: role_id}`."""
    roles = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, _, role_id = pair.partition(":")
        if not name or not role_id:
            raise RuntimeError(f"bad SLIMM_ROLES entry: {pair!r}")
        roles[name.strip()] = role_id.strip()
    return roles


ROLES = parse_roles(os.environ.get("SLIMM_ROLES", ""))

bot = Bot(prefix="!", channels={CHANNEL} if CHANNEL else None)
bot.my_permissions = 0
_role_permissions_cache = {}
# In-memory only - a role command lost across a restart just costs a retype; see README.md.
_last_seq = {}


def listing_message_id():
    return str(uuid.uuid5(LISTING_NAMESPACE, CHANNEL))


def listing_text():
    lines = [
        "**Self-service roles**",
        "`!role <name>` to add one, `!role remove <name>` to drop it, `!role mine` to see what you hold.",
        "",
    ]
    lines.extend(f"- `{name}`" for name in ROLES)
    return "\n".join(lines)


async def post_listing():
    """Publishes the role listing at startup, editing the previous one in place when it already exists."""
    message_id = listing_message_id()
    try:
        await bot.client.call("PATCH", f"/channels/{CHANNEL}/messages/{message_id}", {"content": listing_text()})
    except ApiError as err:
        if not is_not_found(err):
            raise
        await bot.client.send(CHANNEL, listing_text(), message_id=message_id)


async def fetch_role_permissions(role_id):
    """The configured role's own permission bits, or None if unreadable (needs MANAGE_ROLES, or the role is gone)."""
    if role_id in _role_permissions_cache:
        return _role_permissions_cache[role_id]
    try:
        roles = await bot.client.list_roles()
    except ApiError as err:
        if is_forbidden(err):
            return None
        raise
    for role in roles:
        _role_permissions_cache[role["id"]] = role["permissions"]
    return _role_permissions_cache.get(role_id)


async def escalation_explanation(role_name, role_id):
    """Names the exact permission gap - a 403 alone cannot say which guard fired, so this asks `GET /roles` too."""
    if not (bot.my_permissions & Permissions.MANAGE_ROLES):
        return (
            "I can't grant or remove any role here, not even a zero-permission one - I don't hold MANAGE_ROLES "
            "myself. An admin needs to grant this bot's own account MANAGE_ROLES before self-service roles can work at all."
        )
    role_permissions = await fetch_role_permissions(role_id)
    if role_permissions is None:
        return (
            f"I hold MANAGE_ROLES but still can't grant `{role_name}` - either it was deleted, or something else "
            "is wrong. An admin should check it still exists."
        )
    missing = Permissions.names(role_permissions & ~bot.my_permissions)
    if not missing:
        return (
            f"granting `{role_name}` was refused, but I hold everything it carries - an admin should check my "
            "role assignment did not just change."
        )
    named = ", ".join(missing)
    return (
        f"I hold MANAGE_ROLES, but `{role_name}` also carries {named}, which I don't hold myself. An admin needs "
        f"to grant this bot's own account {named} before it can hand out `{role_name}`."
    )


async def grant(ctx, role_name):
    role_id = ROLES.get(role_name)
    if role_id is None:
        await ctx.reply(f"no role called `{role_name}` is offered here - try `!roles`.")
        return
    try:
        await bot.space.grant_role(ctx.author, role_id)
    except ApiError as err:
        if is_forbidden(err):
            await ctx.reply(await escalation_explanation(role_name, role_id))
            return
        if is_not_found(err):
            await ctx.reply(f"`{role_name}` is misconfigured on my end - ask an admin to check it.")
            return
        raise
    await ctx.reply(f"done - you have `{role_name}` now.")


async def revoke(ctx, role_name):
    role_id = ROLES.get(role_name)
    if role_id is None:
        await ctx.reply(f"no role called `{role_name}` is offered here - try `!roles`.")
        return
    try:
        await bot.space.revoke_role(ctx.author, role_id)
    except ApiError as err:
        if is_forbidden(err):
            await ctx.reply(await escalation_explanation(role_name, role_id))
            return
        raise
    await ctx.reply(f"removed `{role_name}`.")


async def show_mine(ctx):
    held = set(ctx.author.role_ids)
    mine = [name for name, role_id in ROLES.items() if role_id in held]
    if mine:
        await ctx.reply(f"you hold: {', '.join(mine)}")
    else:
        await ctx.reply("you hold none of the roles offered here.")


async def show_status(ctx):
    """The same diagnosis a failed grant gives, but on demand and for every configured role at once."""
    lines = [f"I hold: {', '.join(Permissions.names(bot.my_permissions)) or 'nothing'}"]
    if not (bot.my_permissions & Permissions.MANAGE_ROLES):
        lines.append("MANAGE_ROLES is missing, so no role here is grantable yet.")
        await ctx.reply("\n".join(lines))
        return
    for name, role_id in ROLES.items():
        role_permissions = await fetch_role_permissions(role_id)
        if role_permissions is None:
            lines.append(f"`{name}`: cannot verify (role missing or unreadable)")
            continue
        missing = Permissions.names(role_permissions & ~bot.my_permissions)
        lines.append(f"`{name}`: grantable" if not missing else f"`{name}`: missing {', '.join(missing)}")
    await ctx.reply("\n".join(lines))


@bot.command(name="roles", help="List self-service roles, or `status` to diagnose what's grantable", usage="[status]")
async def roles_cmd(ctx, sub: str = None):
    if sub and sub.lower() == "status":
        await show_status(ctx)
        return
    await ctx.reply(listing_text())


@bot.command(name="role", help="Add a role, `remove <name>` to drop it, `mine` to see what you hold", usage="<name> | remove <name> | mine")
async def role_cmd(ctx, first: str, second: str = None):
    action = first.lower()
    if action == "mine":
        await show_mine(ctx)
        return
    if action == "remove" and second:
        await revoke(ctx, second)
        return
    await grant(ctx, first)


async def _resync():
    if CHANNEL not in _last_seq:
        return
    scopes = [{"channel_id": CHANNEL, "after_seq": _last_seq[CHANNEL]}]
    for scope in await catchup.sync(bot.client, scopes):
        for message in scope["messages"]:
            await bot.process_message(message)
        if scope["messages"]:
            _last_seq[CHANNEL] = scope["messages"][-1]["seq"]
        elif scope["reset"]:
            _last_seq.pop(CHANNEL, None)


@bot.event
async def on_raw_message(message):
    seq = message.get("seq")
    if seq is not None:
        _last_seq[CHANNEL] = seq


@bot.event
async def on_connect():
    bot.my_permissions = (await bot.client.me()).get("permissions", 0)
    _role_permissions_cache.clear()
    await _resync()
    await post_listing()


def main():
    if not BASE or not TOKEN or not CHANNEL or not ROLES:
        raise SystemExit("set SLIMM_URL, SLIMM_BOT_TOKEN, SLIMM_CHANNEL and SLIMM_ROLES")
    raise SystemExit(bot.run() or 0)


if __name__ == "__main__":
    main()
