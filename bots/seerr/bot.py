#!/usr/bin/env python3
"""bot-seerr: announces new, approved, declined and available requests, and answers `!request`; see README.md.
Split per docs/framework.md: this is the entry point, seerr_core.py holds the Seerr side, bots/arrkit the shared half."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arrkit import poller  # noqa: E402
from slimbots import ApiError, Bot, Permissions  # noqa: E402
from slimbots.http import is_token_revoked  # noqa: E402

import approvals  # noqa: E402
import seerr_core  # noqa: E402

bot = Bot(prefix="!", require_channels=True, default_data_path="seerr.db", store_migrate=seerr_core.init_db)
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
    for post in await bot.store.run(seerr_core.plan_posts, requests):
        try:
            await send_post(post)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            return
        await bot.store.run(seerr_core.mark_announced, post["keys"])


poller.install(bot, poll_once, seerr_core.SERVICE)


def main():
    poller.main(bot, seerr_core.SERVICE, lambda: seerr_core.permission_problem(Permissions))


if __name__ == "__main__":
    main()
