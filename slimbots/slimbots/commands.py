"""A registered `@bot.command`: argument conversion, cooldowns, and permission gating."""

import inspect

from .converters import Duration, TimeOfDay
from .exceptions import BadArgument, CheckFailure, CommandOnCooldown, MissingPermissions, MissingRequiredArgument
from .http import ApiError
from .limits import Cooldown
from .models import Member

_NO_DEFAULT = inspect.Parameter.empty


def _usage_token(param):
    is_rest = param.annotation is str
    name = param.name
    if param.default is not _NO_DEFAULT:
        return f"[{name}]"
    return f"<{name}...>" if is_rest else f"<{name}>"


class Command:
    """One `@bot.command`-decorated handler, plus everything it was declared with."""

    def __init__(self, func, *, name, aliases=(), help=None, usage=None, cooldown=None, requires=None, check=None):
        self.func = func
        self.name = name
        self.aliases = list(aliases)
        self.help = help or ""
        self.requires = requires
        self.check = check
        self.cooldown = Cooldown(cooldown) if cooldown else None
        self.params = list(inspect.signature(func).parameters.values())[1:]
        self.usage = usage or " ".join(_usage_token(p) for p in self.params)

    @property
    def names(self):
        return [self.name, *self.aliases]

    def check_permission(self, member):
        if self.requires is not None and not member.has_permission(self.requires):
            raise MissingPermissions(self.requires)

    def check_cooldown(self, user_id):
        if self.cooldown is None:
            return
        message = self.cooldown.check(user_id)
        if message:
            raise CommandOnCooldown(message)

    async def _convert(self, ctx, annotation, token, name):
        if annotation is int:
            try:
                return int(token)
            except ValueError as err:
                raise BadArgument(f"`{name}` must be a whole number, got `{token}`") from err
        if annotation is float:
            try:
                return float(token)
            except ValueError as err:
                raise BadArgument(f"`{name}` must be a number, got `{token}`") from err
        if annotation is Member:
            needle = token.lstrip("@")
            member = await ctx.bot.space.get_member(needle)
            if member is None:
                try:
                    member = await ctx.bot.space.fetch_member(needle)
                except ApiError:
                    member = None
            if member is None:
                raise BadArgument(f"no member called `{token}` here")
            return member
        if annotation is Duration:
            parsed = Duration.parse(token)
            if parsed is None:
                raise BadArgument(f"`{name}` must be a duration like `10m` or `2h30m`, got `{token}`")
            return parsed
        if annotation is TimeOfDay:
            parsed = TimeOfDay.parse(token)
            if parsed is None:
                raise BadArgument(f"`{name}` must be a time like `14:30`, got `{token}`")
            return parsed
        return token

    async def convert_args(self, ctx, raw_args):
        """Converts each whitespace-split token per the signature; see docs/framework.md."""
        tokens = raw_args.split()
        values = []
        i = 0
        for idx, param in enumerate(self.params):
            annotation = param.annotation if param.annotation is not _NO_DEFAULT else str
            is_last_rest = idx == len(self.params) - 1 and annotation is str
            if is_last_rest:
                rest = " ".join(tokens[i:])
                if not rest:
                    if param.default is not _NO_DEFAULT:
                        values.append(param.default)
                    else:
                        raise MissingRequiredArgument(param.name)
                else:
                    values.append(rest)
                i = len(tokens)
                continue
            if i >= len(tokens):
                if param.default is not _NO_DEFAULT:
                    values.append(param.default)
                    continue
                raise MissingRequiredArgument(param.name)
            values.append(await self._convert(ctx, annotation, tokens[i], param.name))
            i += 1
        return values

    async def invoke(self, ctx):
        self.check_permission(ctx.author)
        self.check_cooldown(ctx.author.id)
        if self.check is not None and not await _maybe_await(self.check(ctx)):
            raise CheckFailure("you can't use that command right now")
        args = await self.convert_args(ctx, ctx.raw_args)
        return await self.func(ctx, *args)


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


class Group(Command):
    """A command that dispatches its first argument token to `@group.command()`-registered subcommands."""

    def __init__(self, func, *, name, aliases=(), help=None, cooldown=None, requires=None, check=None):
        self.func = func
        self.name = name
        self.aliases = list(aliases)
        self.help = help or ""
        self.requires = requires
        self.check = check
        self.cooldown = Cooldown(cooldown) if cooldown else None
        self.params = list(inspect.signature(func).parameters.values())[1:]
        self.subcommands = {}
        self._unique_subcommands = []

    def command(self, name=None, *, aliases=(), help=None, usage=None, cooldown=None, requires=None, check=None):
        def decorator(func):
            sub = Command(
                func, name=name or func.__name__, aliases=aliases, help=help,
                usage=usage, cooldown=cooldown, requires=requires, check=check,
            )
            for sub_name in sub.names:
                if sub_name in self.subcommands:
                    raise ValueError(f"subcommand name/alias `{sub_name}` is already registered under `{self.name}`")
                self.subcommands[sub_name] = sub
            self._unique_subcommands.append(sub)
            return func
        return decorator

    def unique_subcommands(self):
        return list(self._unique_subcommands)

    @property
    def usage(self):
        if not self._unique_subcommands:
            return " ".join(_usage_token(p) for p in self.params)
        return "|".join(s.name for s in self._unique_subcommands) + " ..."

    async def invoke(self, ctx):
        """Dispatches by the first token of `ctx.raw_args`; anything else falls to the group's own handler."""
        self.check_permission(ctx.author)
        self.check_cooldown(ctx.author.id)
        if self.check is not None and not await _maybe_await(self.check(ctx)):
            raise CheckFailure("you can't use that command right now")
        name_token, _, remainder = ctx.raw_args.partition(" ")
        subcommand = self.subcommands.get(name_token) if name_token else None
        if subcommand is None:
            args = await self.convert_args(ctx, ctx.raw_args)
            return await self.func(ctx, *args)
        ctx.raw_args = remainder
        ctx.invoked_with = f"{self.name} {subcommand.name}"
        return await subcommand.invoke(ctx)


def build_help_text(bot, *, command_name=None):
    """The auto-generated `help` command's reply: one command or subcommand, or the full list."""
    if command_name:
        return _describe(bot, command_name)
    lines = [f"**Commands** (prefix `{bot.prefix}`)"]
    for command in bot.unique_commands():
        usage = f" {command.usage}" if command.usage else ""
        summary = f" - {command.help}" if command.help else ""
        lines.append(f"`{bot.prefix}{command.name}{usage}`{summary}")
    return "\n".join(lines)


def _describe(bot, command_name):
    """One command's help, descending into a subcommand when `command_name` names one."""
    head, _, sub_name = command_name.partition(" ")
    command = bot.get_command(head)
    if command is None:
        return f"no command called `{head}`"
    if sub_name:
        subcommand = command.subcommands.get(sub_name) if isinstance(command, Group) else None
        if subcommand is None:
            return f"no subcommand called `{sub_name}` under `{head}`"
        return _render(bot, f"{head} {subcommand.name}", subcommand)
    lines = [_render(bot, command.name, command)]
    if isinstance(command, Group) and command.unique_subcommands():
        lines.append("subcommands: " + ", ".join(s.name for s in command.unique_subcommands()))
    return "\n".join(lines)


def _render(bot, display_name, command):
    usage = f" {command.usage}" if command.usage else ""
    lines = [f"`{bot.prefix}{display_name}{usage}`".rstrip()]
    if command.aliases:
        lines.append(f"aliases: {', '.join(command.aliases)}")
    if command.help:
        lines.append(command.help)
    return "\n".join(lines)
