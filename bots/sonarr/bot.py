#!/usr/bin/env python3
"""bot-sonarr: announces grabbed, downloaded, upgraded and failed episodes, and answers `!sonarr`; see README.md.
Split per docs/framework.md: this is the entry point, sonarr_core.py holds the Sonarr side, bots/arrkit the shared half."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arrkit import poller  # noqa: E402
from slimbots import Bot  # noqa: E402

import sonarr_core  # noqa: E402

bot = Bot(prefix="!", require_channels=True, default_data_path="sonarr.db", store_migrate=sonarr_core.init_db)
sonarr_core.configure(bot)
bot.load_extension("sonarr_cog")


async def poll_once():
    await poller.history_poll_once(bot, sonarr_core)


poller.install(bot, poll_once, sonarr_core.SERVICE)


def main():
    poller.main(bot, sonarr_core.SERVICE)


if __name__ == "__main__":
    main()
