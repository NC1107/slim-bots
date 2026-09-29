#!/usr/bin/env python3
"""bot-music: plays Jellyfin tracks and direct stream urls into the invoker's voice call; see README.md.
Split per docs/framework.md: this is the entry point, music_core.py/player.py/music_cog.py hold the rest."""

from slimbots import Bot

import music_core

# A different default prefix from the jellyfin bot, whose `!stop`/`!np`/`!pause` would otherwise both answer.
bot = Bot(prefix="~", require_channels=True, listen_voice_chats=True)
music_core.configure(bot)

bot.load_extension("music_cog")


def main():
    problem = music_core.check_config()
    if problem:
        raise SystemExit(problem)
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
