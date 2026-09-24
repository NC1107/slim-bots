"""`Bot`: the one constructor a script needs - config, auth, the websocket,
reconnect, command dispatch, and `run()`.
"""

import asyncio
import os
import sys

from .authors import AuthorFilter
from .commands import Command, build_help_text
from .context import Context
from .exceptions import CommandError
from .gateway import Gateway
from .http import ApiError, AsyncClient, is_forbidden, is_token_revoked
from .lifecycle import guard_dispatch, run_with_shutdown
from .registration import register_commands
from .space import Space

DEFAULT_USER_AGENT = "slimbots/0.3"

# Deployment-wide frame types dispatched as events; see docs/framework.md on why there is no on_member_join.
_EVENT_FRAMES = {
    "member.removed": "on_member_removed",
    "member.restored": "on_member_restored",
    "member.role_changed": "on_member_role_changed",
    "role.changed": "on_role_changed",
    "member.timeout": "on_member_timeout",
}


class Bot:
    """Owns everything a bot script would otherwise wire up by hand."""

    def __init__(self, prefix="!", *, url=None, token=None, user_agent=DEFAULT_USER_AGENT,
                 ignore_bots=True, base_delay=1.0, max_delay=60.0, help_command=True, channels=None):
        self.prefix = prefix
        self._url = url
        self._token = token
        self.user_agent = user_agent
        self.ignore_bots = ignore_bots
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.channels = set(channels) if channels else None
        self.commands = {}
        self._unique_commands = []
        self._listeners = {}
        self._global_checks = []
        self.client = None
        self.space = None
        self.authors = None
        self.me_id = None
        if help_command:
            self._register_default_help()

    def check(self, func):
        """Registers an async predicate run before every command; a truthy string return refuses with that reply."""
        self._global_checks.append(func)
        return func

    def command(self, name=None, *, aliases=(), help=None, usage=None, cooldown=None, requires=None, check=None):
        def decorator(func):
            self.add_command(Command(
                func, name=name or func.__name__, aliases=aliases, help=help,
                usage=usage, cooldown=cooldown, requires=requires, check=check,
            ))
            return func
        return decorator

    def add_command(self, command):
        for name in command.names:
            if name in self.commands:
                raise ValueError(f"command name/alias `{name}` is already registered")
            self.commands[name] = command
        self._unique_commands.append(command)

    def get_command(self, name):
        return self.commands.get(name)

    def unique_commands(self):
        return list(self._unique_commands)

    def event(self, func=None, *, name=None):
        def decorator(f):
            self._listeners.setdefault(name or f.__name__, []).append(f)
            return f
        return decorator(func) if func else decorator

    def _register_default_help(self):
        @self.command(name="help", help="Show this list, or one command's usage")
        async def help_command(ctx, command_name: str = None):
            await ctx.reply(build_help_text(self, command_name=command_name))

    async def _dispatch_event(self, name, *args):
        for handler in self._listeners.get(name, []):
            await guard_dispatch(handler, *args)

    async def _resolve_author(self, author_id):
        member = self.space.members.get(author_id)
        if member is not None:
            return member
        try:
            return await self.space.fetch_member(author_id)
        except ApiError:
            return None

    async def process_message(self, message):
        """One `message.created` payload: the bot-ignore default, command
        parsing, argument conversion, and dispatch to the matched handler."""
        author_id = message.get("author_id")
        if not author_id or author_id == self.me_id:
            return
        if self.ignore_bots and await self.authors.is_automated(author_id):
            return

        content = message.get("content") or ""
        if not content.startswith(self.prefix):
            await self._dispatch_event("on_message", message)
            return

        invoked_with, _, raw_args = content[len(self.prefix):].partition(" ")
        command = self.commands.get(invoked_with)
        if command is None:
            return

        author = await self._resolve_author(author_id)
        if author is None:
            return
        ctx = Context(
            bot=self, message=message, author=author, channel_id=message.get("channel_id"),
            command=command, invoked_with=invoked_with, raw_args=raw_args,
        )
        await self._invoke(ctx, command)

    async def _invoke(self, ctx, command):
        try:
            for global_check in self._global_checks:
                refusal = await global_check(ctx)
                if refusal:
                    await ctx.reply(refusal)
                    return
            await command.invoke(ctx)
        except CommandError as err:
            await ctx.reply(str(err))
            await self._dispatch_event("on_command_error", ctx, err)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            reply = f"I can't do that: {err.reason or 'missing permission'}" if is_forbidden(err) \
                else "something went wrong running that command"
            await ctx.reply(reply)
            await self._dispatch_event("on_command_error", ctx, err)
        except Exception as err:
            print(f"unhandled error in command `{command.name}`: {err}", file=sys.stderr)
            await self._dispatch_event("on_command_error", ctx, err)

    async def _handle_frame(self, frame):
        kind = frame.get("type")
        if kind == "message.created":
            if self.channels is not None and frame.get("channel_id") not in self.channels:
                return
            message = frame.get("message") or {}
            await guard_dispatch(self._dispatch_event, "on_raw_message", message)
            await guard_dispatch(self.process_message, message)
            return
        event_name = _EVENT_FRAMES.get(kind)
        if event_name:
            await guard_dispatch(self._dispatch_event, event_name, frame)

    async def _connect_once(self, reset_delay):
        self.me_id = (await self.client.me())["id"]
        await self.space.refresh_channels()
        try:
            await self.space.refresh_roles()
        except ApiError as err:
            if not is_forbidden(err):
                raise
        await self.space.refresh_members()
        await register_commands(self.client, prefix=self.prefix, commands=self.unique_commands())
        await self._dispatch_event("on_connect")

        async with await Gateway.open(self.client) as gateway:
            reset_delay()
            await self._dispatch_event("on_ready")
            async for frame in gateway.frames():
                await self._handle_frame(frame)

    async def _run_forever(self):
        delay = self.base_delay

        def reset_delay():
            nonlocal delay
            delay = self.base_delay

        while True:
            try:
                await self._connect_once(reset_delay)
            except ApiError as err:
                if is_token_revoked(err):
                    print("token rejected - revoked?", file=sys.stderr)
                    return 1
                print(f"{type(err).__name__}: {err}, retrying in {delay}s", file=sys.stderr)
            except Exception as err:
                print(f"{type(err).__name__}: {err}, retrying in {delay}s", file=sys.stderr)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.max_delay)

    async def start(self, *, url=None, token=None):
        base = url or self._url or os.environ.get("SLIMM_URL", "")
        token = token or self._token or os.environ.get("SLIMM_BOT_TOKEN", "")
        if not base or not token:
            raise RuntimeError("set SLIMM_URL and SLIMM_BOT_TOKEN, or pass url=/token= to Bot()")
        self.client = AsyncClient(base, token, self.user_agent)
        self.space = Space(self.client)
        self.authors = AuthorFilter(self.client, space=self.space, ignore_bots=self.ignore_bots)
        try:
            return await run_with_shutdown(self._run_forever)
        finally:
            await self.client.aclose()

    def run(self, *, url=None, token=None):
        return asyncio.run(self.start(url=url, token=token)) or 0
