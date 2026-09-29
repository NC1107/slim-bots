#!/usr/bin/env python3
"""bot-radarr: announces grabbed, downloaded, upgraded and failed movies, and answers `!radarr`; see README.md.
Split per docs/framework.md: this is the entry point and the poll loop, radarr_core.py holds the rest."""

import asyncio
import sys

from slimbots import ApiError, Bot, Embed
from slimbots.http import is_token_revoked

import radarr_core

bot = Bot(
    prefix="!", require_channels=True,
    default_data_path="radarr.db", store_migrate=radarr_core.init_db,
)
radarr_core.configure(bot)

bot.load_extension("radarr_cog")


async def send_post(post):
    assert bot.client is not None, "send_post runs only once connected"
    text = radarr_core.render_text(post)
    embed = Embed(title=text, color=radarr_core.COLORS[post["kind"]])
    await bot.client.send(bot.channel, "", message_id=post["message_id"], embeds=[embed.to_wire()], fallback_content=text)


async def poll_once():
    cursor = await bot.store.run(radarr_core.get_cursor)
    if cursor is None:
        await bot.store.run(radarr_core.bootstrap_cursor)
        return
    records = await asyncio.to_thread(radarr_core.fetch_history_since, cursor)
    if not records:
        return
    events = await bot.store.run(radarr_core.plan_events, records)
    for post in radarr_core.build_posts(events):
        try:
            await send_post(post)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            return
        await bot.store.run(radarr_core.mark_announced, post["marks"])
    await bot.store.run(radarr_core.advance_cursor, records[-1]["id"])


async def poll_loop():
    """A rejected Radarr key or revoked slimm token propagates out - bot.background() treats that as fatal."""
    while True:
        try:
            await poll_once()
        except radarr_core.RadarrAuthError:
            print("radarr api key rejected - exiting", file=sys.stderr)
            raise
        except ApiError as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        await asyncio.sleep(radarr_core.RADARR_POLL_SECONDS)


_background_started = False


@bot.event
async def on_connect():
    global _background_started
    if _background_started:
        return
    _background_started = True
    bot.background(poll_loop(), name="radarr-poll")


def main():
    problem = radarr_core.check_radarr_config()
    if problem:
        raise SystemExit(problem)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
