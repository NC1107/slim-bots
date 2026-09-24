"""Errors a command handler can raise or receive; see docs/framework.md."""


class SlimBotsError(Exception):
    """Base for every error this package raises on purpose."""


class CommandError(SlimBotsError):
    """Base for a failure during command dispatch, always user-facing."""


class CommandNotFound(CommandError):
    """No registered command matches the invoked name or alias; dispatched to on_command_not_found, silent by default."""

    def __init__(self, invoked_with):
        super().__init__(f"no command called `{invoked_with}`")
        self.invoked_with = invoked_with


class BadArgument(CommandError):
    """An argument failed conversion; `str(self)` is the reply to send."""


class MissingRequiredArgument(BadArgument):
    """A command was invoked without enough words to fill its signature."""

    def __init__(self, name):
        super().__init__(f"missing a value for `{name}`")
        self.name = name


class CommandOnCooldown(CommandError):
    """A cooldown or rate limit rejected this invocation."""

    def __init__(self, retry_message):
        super().__init__(retry_message)


class MissingPermissions(CommandError):
    """The invoking member lacks a permission the command requires."""

    def __init__(self, permission):
        super().__init__("you do not have permission to use that command")
        self.permission = permission


class CheckFailure(CommandError):
    """A custom `@bot.command(check=...)` predicate returned false."""
