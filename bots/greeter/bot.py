#!/usr/bin/env python3
"""bot-greeter: posts a configurable welcome message when someone joins; see README.md."""

from slimbots import Bot, Embed

bot = Bot(prefix="!", require_channels=True)

GREETER_MESSAGE = bot.setting("GREETER_MESSAGE", "Welcome, {member}! Make yourself at home.")
GREETER_TITLE = bot.setting("GREETER_TITLE", "New member")


def welcome_text(member):
    """`{member}` in `GREETER_MESSAGE` becomes an @mention; any other `{...}` is left to a KeyError, logged not fatal."""
    return GREETER_MESSAGE.format(member=member.mention())


@bot.event
async def on_member_join(member):
    text = welcome_text(member)
    embed = Embed(title=GREETER_TITLE, description=text)
    await bot.client.send(bot.channel, text, embeds=[embed.to_wire()], fallback_content=text)


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
