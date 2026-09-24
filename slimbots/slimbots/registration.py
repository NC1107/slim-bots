"""Publishing a bot's prefix and commands (decision 0031); see docs/framework.md."""

from .http import ApiError

MAX_REGISTERED_COMMANDS = 50


def registration_body(prefix, commands):
    """Every registered name and alias as its own composer entry, capped at the server's limit."""
    entries = []
    for command in commands:
        for name in command.names:
            if len(entries) >= MAX_REGISTERED_COMMANDS:
                break
            entry = {"name": name, "description": command.help or command.name}
            if command.usage:
                entry["usage"] = command.usage
            if command.requires is not None:
                entry["permission"] = int(command.requires)
            entries.append(entry)
    return {"prefix": prefix, "commands": entries}


async def register_commands(client, *, prefix, commands):
    """PUT /bots/commands with the bot's whole set; returns False, quietly,
    against a server old enough not to have the route at all."""
    try:
        await client.call("PUT", "/bots/commands", registration_body(prefix, commands))
        return True
    except ApiError as err:
        if err.status == 404:
            return False
        raise
