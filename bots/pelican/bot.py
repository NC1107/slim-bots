#!/usr/bin/env python3
"""bot-pelican: `!servers`, `!server <name>`, role-gated `!start|!stop|!restart <name>`, and a self-editing status message; see README.md.
Split per docs/framework.md: this is the entry point and the status loop, pelican_core.py holds the rest."""

import asyncio
import sys
import uuid

from slimbots import ApiError, Bot
from slimbots.http import is_not_found, is_token_revoked

import pelican_core as core

STATUS_NAMESPACE = uuid.UUID("70656c69-6361-6e2d-7374-617475732d31")
LIST_COOLDOWN_SECONDS = 10
POWER_COOLDOWN_SECONDS = 15

bot = Bot(prefix="!")
core.configure(bot)
_last_status_text = None


async def snapshot(servers):
    """Each server paired with its resource stats, or None where the panel could not answer for that one."""

    async def one(server):
        try:
            return await asyncio.to_thread(core.fetch_resources, server["identifier"])
        except core.PelicanAuthError:
            raise
        except core.PelicanError:
            return None

    return list(zip(servers, await asyncio.gather(*(one(server) for server in servers))))


async def resolve(ctx, query):
    """The one server `query` names, or None after replying why not."""
    try:
        servers = await asyncio.to_thread(core.list_servers)
    except core.PelicanError as err:
        await ctx.reply(f"can't reach the panel: {err}")
        return None
    matches = core.find_servers(servers, query)
    if not matches:
        await ctx.reply(f"no server matches `{query}` - try `{bot.prefix}servers`.")
        return None
    if len(matches) > 1:
        names = ", ".join(f"`{server['name']}`" for server in matches[:8])
        await ctx.reply(f"`{query}` matches more than one server: {names}. Be more specific.")
        return None
    return matches[0]


@bot.command(name="servers", help="List game servers with state, CPU and RAM", cooldown=LIST_COOLDOWN_SECONDS)
async def servers_cmd(ctx):
    try:
        servers = await asyncio.to_thread(core.list_servers)
        rows = await snapshot(servers)
    except core.PelicanError as err:
        await ctx.reply(f"can't reach the panel: {err}")
        return
    if not rows:
        await ctx.reply("the panel has no servers I may show.")
        return
    await ctx.reply("\n".join(core.summary_line(server, stats) for server, stats in rows))


@bot.command(name="server", help="Show one game server in detail", usage="<name>", cooldown=LIST_COOLDOWN_SECONDS)
async def server_cmd(ctx, name: str):
    server = await resolve(ctx, name)
    if server is None:
        return
    try:
        stats = await asyncio.to_thread(core.fetch_resources, server["identifier"])
    except core.PelicanError as err:
        await ctx.reply(f"can't read `{server['name']}`: {err}")
        return
    await ctx.reply(core.detail_text(server, stats))


def audit_line(ctx, signal, server, outcome):
    return f"pelican power: `{signal}` on `{server['name']}` by {ctx.author.mention()} ({ctx.author.id}) - {outcome}"


async def send_audit(ctx, signal, server, outcome):
    """True once the audit line is posted; a power action never runs without its log entry."""
    try:
        await bot.client.send(core.PELICAN_LOG_CHANNEL, audit_line(ctx, signal, server, outcome))
    except ApiError as err:
        if is_token_revoked(err):
            raise
        return False
    return True


async def power(ctx, signal, query):
    if not core.may_control(ctx.author):
        await ctx.reply(core.control_denied_text())
        return
    if not core.PELICAN_LOG_CHANNEL:
        await ctx.reply("power commands are off until an admin sets PELICAN_LOG_CHANNEL.")
        return
    server = await resolve(ctx, query)
    if server is None:
        return
    if signal != "start" and not await ctx.confirm(f"{signal} `{server['name']}`?"):
        await ctx.reply("cancelled.")
        return
    if not await send_audit(ctx, signal, server, "requested"):
        await ctx.reply("I can't write to the log channel, so I won't run this.")
        return
    try:
        await asyncio.to_thread(core.api_power, server["identifier"], signal)
    except core.PelicanError as err:
        await send_audit(ctx, signal, server, f"failed ({err})")
        await ctx.reply(f"`{signal}` on `{server['name']}` failed: {err}")
        return
    await ctx.reply(f"sent `{signal}` to `{server['name']}`.")


@bot.command(name="start", help="Start a game server (control role only)", usage="<name>", cooldown=POWER_COOLDOWN_SECONDS)
async def start_cmd(ctx, name: str):
    await power(ctx, "start", name)


@bot.command(name="stop", help="Stop a game server (control role only)", usage="<name>", cooldown=POWER_COOLDOWN_SECONDS)
async def stop_cmd(ctx, name: str):
    await power(ctx, "stop", name)


@bot.command(name="restart", help="Restart a game server (control role only)", usage="<name>", cooldown=POWER_COOLDOWN_SECONDS)
async def restart_cmd(ctx, name: str):
    await power(ctx, "restart", name)


def status_text(rows):
    if not rows:
        return "**Game servers**\nthe panel has no servers I may show."
    return "**Game servers**\n" + "\n".join(core.summary_line(server, stats) for server, stats in rows)


def status_message_id():
    """Derived from the channel id, so the same message is found again after a restart with nothing persisted."""
    return str(uuid.uuid5(STATUS_NAMESPACE, core.PELICAN_STATUS_CHANNEL))


async def publish_status(text):
    """Edits the one status message in place, creating it the first time; unchanged text is not re-sent."""
    global _last_status_text
    if text == _last_status_text:
        return
    message_id = status_message_id()
    try:
        await bot.client.edit_message(core.PELICAN_STATUS_CHANNEL, message_id, text)
    except ApiError as err:
        if not is_not_found(err):
            raise
        await bot.client.send(core.PELICAN_STATUS_CHANNEL, text, message_id=message_id)
    _last_status_text = text


async def status_once():
    servers = await asyncio.to_thread(core.list_servers)
    await publish_status(status_text(await snapshot(servers)))


async def status_loop():
    """A rejected panel key or slim-m token propagates out - bot.background() treats that as fatal."""
    while True:
        try:
            await status_once()
        except core.PelicanAuthError:
            print("pelican api key rejected - exiting", file=sys.stderr)
            raise
        except ApiError as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        except core.PelicanError as err:
            print(f"pelican: {err}, retrying next cycle", file=sys.stderr)
        await asyncio.sleep(core.PELICAN_STATUS_INTERVAL)


_background_started = False


@bot.event
async def on_connect():
    global _background_started
    if _background_started or not core.PELICAN_STATUS_CHANNEL:
        return
    _background_started = True
    bot.background(status_loop(), name="pelican-status")


def main():
    problem = core.check_config()
    if problem:
        raise SystemExit(problem)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
