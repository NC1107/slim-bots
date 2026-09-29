#!/usr/bin/env python3
"""bot-seerr: announces new, approved, declined and available requests, and answers `!request`; see README.md.
Split per docs/framework.md: this is the entry point and the poll loop, seerr_core.py holds the rest."""

import asyncio
import sys

from slimbots import ApiError, Bot, Permissions
from slimbots.http import is_token_revoked

import approvals
import seerr_core

bot = Bot(
    prefix="!", require_channels=True,
    default_data_path="seerr.db", store_migrate=seerr_core.init_db,
)
seerr_core.configure(bot)

bot.load_extension("seerr_cog")
bot.button(prefix=approvals.ID_PREFIX)(approvals.on_decision_press)


async def send_post(post):
    assert bot.client is not None, "send_post runs only once connected"
    text = seerr_core.render_text(post)
    layout = approvals.buttons_for(post["request_id"]) if post["buttons"] else None
    message = await bot.client.send(bot.channel, text, message_id=post["message_id"], components=layout)
    if layout:
        approvals.remember(message.id, text)


async def poll_once():
    requests = await asyncio.to_thread(seerr_core.fetch_recent_requests)
    if await bot.store.run(seerr_core.bootstrap, requests):
        return
    posts = await bot.store.run(seerr_core.plan_posts, requests)
    for post in posts:
        try:
            await send_post(post)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            return
        await bot.store.run(seerr_core.mark_announced, post["keys"])


async def poll_loop():
    """A rejected Seerr key or revoked slimm token propagates out - bot.background() treats that as fatal."""
    while True:
        try:
            await poll_once()
        except seerr_core.SeerrAuthError:
            print("seerr api key rejected - exiting", file=sys.stderr)
            raise
        except ApiError as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying next cycle", file=sys.stderr)
        await asyncio.sleep(seerr_core.SEERR_POLL_SECONDS)


_background_started = False


@bot.event
async def on_connect():
    global _background_started
    if _background_started:
        return
    _background_started = True
    bot.background(poll_loop(), name="seerr-poll")


def main():
    problem = seerr_core.check_seerr_config(Permissions)
    if problem:
        raise SystemExit(problem)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
