"""Publishing a bot's prefix and commands (decision 0031); see docs/framework.md."""

from .http import ApiError

MAX_REGISTERED_COMMANDS = 50
MAX_DESCRIPTION_LEN = 100
MAX_USAGE_LEN = 80


def registration_body(prefix, commands):
    """Every registered name and alias as its own composer entry; a group still counts as just one."""
    entries = []
    for command in commands:
        for name in command.names:
            if len(entries) >= MAX_REGISTERED_COMMANDS:
                break
            entries.append(_entry(command, name))
    return {"prefix": prefix, "commands": entries}


def _entry(command, name):
    description = (command.help or command.name)[:MAX_DESCRIPTION_LEN]
    entry = {"name": name, "description": description}
    if command.usage:
        entry["usage"] = command.usage[:MAX_USAGE_LEN]
    if command.requires is not None:
        entry["permission"] = int(command.requires)
    return entry


async def register_commands(client, *, prefix, commands):
    """PUT /bots/commands with the bot's whole set; returns False, quietly, against a server too old to have the route."""
    try:
        await client.call("PUT", "/bots/commands", registration_body(prefix, commands))
        return True
    except ApiError as err:
        if err.status in (404, 405):
            return False
        raise
