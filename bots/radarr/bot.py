#!/usr/bin/env python3
"""bot-radarr: announces grabbed, downloaded, upgraded and failed movies, and answers `!radarr`; see README.md.
Split per docs/framework.md: this is the entry point, radarr_core.py holds the Radarr side, bots/arrkit the shared half."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arrkit import poller  # noqa: E402
from slimbots import Bot  # noqa: E402

import radarr_core  # noqa: E402

bot = Bot(prefix="!", require_channels=True, default_data_path="radarr.db", store_migrate=radarr_core.init_db)
radarr_core.configure(bot)
bot.load_extension("radarr_cog")


async def poll_once():
    await poller.history_poll_once(bot, radarr_core)


poller.install(bot, poll_once, radarr_core.SERVICE)


def main():
    poller.main(bot, radarr_core.SERVICE)


if __name__ == "__main__":
    main()
