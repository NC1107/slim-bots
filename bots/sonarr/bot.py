#!/usr/bin/env python3
"""bot-sonarr: announces grabbed, downloaded, upgraded and failed episodes, and answers `!sonarr`; see README.md.
Split per docs/framework.md: this is the entry point and the poll loop, sonarr_core.py holds the rest."""

import asyncio
import sys

from slimbots import ApiError, Bot, Embed
from slimbots.http import is_token_revoked

import sonarr_core

bot = Bot(
    prefix="!", require_channels=True,
    default_data_path="sonarr.db", store_migrate=sonarr_core.init_db,
)
sonarr_core.configure(bot)

bot.load_extension("sonarr_cog")


async def send_post(post):
    assert bot.client is not None, "send_post runs only once connected"
    text = sonarr_core.render_text(post)
    embed = Embed(title=text, color=sonarr_core.COLORS[post["kind"]])
    await bot.client.send(bot.channel, "", message_id=post["message_id"], embeds=[embed.to_wire()], fallback_content=text)


async def poll_once():
    cursor = await bot.store.run(sonarr_core.get_cursor)
    if cursor is None:
        await bot.store.run(sonarr_core.bootstrap_cursor)
        return
    records = await asyncio.to_thread(sonarr_core.fetch_history_since, cursor)
    if not records:
        return
    events = await bot.store.run(sonarr_core.plan_events, records)
    for post in sonarr_core.build_posts(events):
        try:
            await send_post(post)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            return
        await bot.store.run(sonarr_core.mark_announced, post["marks"])
    await bot.store.run(sonarr_core.advance_cursor, records[-1]["id"])


async def poll_loop():
    """A rejected Sonarr key or revoked slimm token propagates out - bot.background() treats that as fatal."""
    while True:
        try:
            await poll_once()
        except sonarr_core.SonarrAuthError:
            print("sonarr api key rejected - exiting", file=sys.stderr)
            raise
        except ApiError as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        await asyncio.sleep(sonarr_core.SONARR_POLL_SECONDS)


_background_started = False


@bot.event
async def on_connect():
    global _background_started
    if _background_started:
        return
    _background_started = True
    bot.background(poll_loop(), name="sonarr-poll")


def main():
    problem = sonarr_core.check_sonarr_config()
    if problem:
        raise SystemExit(problem)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
