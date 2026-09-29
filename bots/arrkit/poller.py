"""The poll loops: fatal on a rejected key or revoked token, otherwise retry next cycle."""

import asyncio
import sys

from slimbots import ApiError, Embed
from slimbots.http import is_token_revoked

from . import history, store
from .service import AuthError


async def send_history_post(bot, post):
    assert bot.client is not None, "send_history_post runs only once connected"
    text = history.render_text(post)
    embed = Embed(title=text, color=history.COLORS[post["kind"]])
    await bot.client.send(bot.channel, "", message_id=post["message_id"], embeds=[embed.to_wire()], fallback_content=text)


async def history_poll_once(bot, core):
    """Announces new Sonarr/Radarr history; `core` supplies `api`, `fetch_history_since`, `plan_events`, `build_posts`."""
    cursor = await bot.store.run(store.get_cursor)
    if cursor is None:
        await bot.store.run(core.bootstrap_cursor)
        return
    records = await asyncio.to_thread(core.fetch_history_since, cursor)
    if not records:
        return
    events = await bot.store.run(core.plan_events, records)
    for post in core.build_posts(events):
        try:
            await send_history_post(bot, post)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            return
        await bot.store.run(store.mark_announced, post["marks"])
    await bot.store.run(store.advance_cursor, records[-1]["id"])


async def run_forever(poll_once, service):
    """A rejected service key or revoked slimm token propagates out - bot.background() treats that as fatal."""
    while True:
        try:
            await poll_once()
        except AuthError:
            print(f"{service.name} api key rejected - exiting", file=sys.stderr)
            raise
        except ApiError as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        await asyncio.sleep(service.poll_seconds)


def install(bot, poll_once, service):
    """Starts the poll loop the first time the bot connects; a reconnect must not start a second."""
    started = []

    async def on_connect():
        if started:
            return
        started.append(True)
        bot.background(run_forever(poll_once, service), name=f"{service.name}-poll")

    bot.event(on_connect)


def main(bot, service, extra_problem=None):
    """The entry point every bot here shares: refuse an unsafe url or setting, then run until told to stop."""
    problem = service.problem() or (extra_problem() if extra_problem else None)
    if problem:
        raise SystemExit(problem)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))
