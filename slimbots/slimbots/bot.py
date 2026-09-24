"""`Bot`: the one constructor a script needs - config, auth, the websocket,
reconnect, command dispatch, and `run()`.
"""

import asyncio
import os
import sqlite3
import sys

from . import catchup, cursor
from . import events as ev
from .authors import AuthorFilter
from .commands import Command, Group, build_help_text
from .context import Context
from .exceptions import CommandError
from .gateway import Gateway
from .http import ApiError, AsyncClient, is_forbidden, is_token_revoked
from .lifecycle import guard_dispatch, run_with_shutdown
from .registration import register_commands
from .space import Space

DEFAULT_USER_AGENT = "slimbots/0.3"
DEFAULT_CURSOR_DB = "slimbots-cursor.db"

# Deployment-wide (or DM/user-scoped) frame types: (handler name, payload class or None for the raw frame).
# See docs/framework.md on why there is no on_member_join.
_GLOBAL_EVENT_FRAMES = {
    "member.removed": ("on_member_removed", None),
    "member.restored": ("on_member_restored", None),
    "member.role_changed": ("on_member_role_changed", None),
    "role.changed": ("on_role_changed", None),
    "member.timeout": ("on_member_timeout", None),
    "presence.changed": ("on_presence_changed", ev.PresenceChanged),
    "profile.changed": ("on_profile_changed", ev.ProfileChanged),
    "channel.created": ("on_channel_created", ev.ChannelCreated),
    "channel.updated": ("on_channel_updated", ev.ChannelUpdated),
    "channel.deleted": ("on_channel_deleted", ev.ChannelDeleted),
    "category.changed": ("on_category_changed", ev.CategoryChanged),
    "call.ringing": ("on_call_ringing", ev.CallRinging),
    "call.ring_ended": ("on_call_ring_ended", ev.CallRingEnded),
    "reports.changed": ("on_reports_changed", ev.ReportsChanged),
}

# Channel-scoped frame types - dispatched only for a channel in `channels`, the same gate message.created gets.
_CHANNEL_EVENT_FRAMES = {
    "canvas.object.placed": ("on_canvas_object_placed", None),
    "canvas.objects.removed": ("on_canvas_objects_removed", None),
    "canvas.cleared": ("on_canvas_cleared", None),
    "message.edited": ("on_message_edited", ev.MessageEdited),
    "message.deleted": ("on_message_deleted", ev.MessageDeleted),
    "reactions.changed": ("on_reactions_changed", ev.ReactionsChanged),
    "thread.updated": ("on_thread_updated", ev.ThreadUpdated),
    "message.pinned": ("on_message_pinned", ev.MessagePinned),
    "message.unpinned": ("on_message_unpinned", ev.MessageUnpinned),
    "poll.voted": ("on_poll_voted", ev.PollVoted),
    "typing.started": ("on_typing_started", ev.TypingStarted),
    "typing.stopped": ("on_typing_stopped", ev.TypingStopped),
    "overwrite.changed": ("on_overwrite_changed", ev.OverwriteChanged),
    "voice.activity": ("on_voice_activity", ev.VoiceActivityChanged),
    "canvas.objects.restored": ("on_canvas_objects_restored", ev.CanvasObjectsRestored),
    "canvas.cursor.moved": ("on_canvas_cursor_moved", ev.CanvasCursorMoved),
    "canvas.stroke_preview.updated": ("on_canvas_stroke_preview_updated", ev.CanvasStrokePreviewUpdated),
    "canvas.object.moved": ("on_canvas_object_moved", ev.CanvasObjectMoved),
    "canvas.object.reordered": ("on_canvas_object_reordered", ev.CanvasObjectReordered),
    "canvas.media_slot.changed": ("on_canvas_media_slot_changed", ev.CanvasMediaSlotChanged),
}


class Bot:
    """Owns everything a bot script would otherwise wire up by hand."""

    def __init__(self, prefix="!", *, url=None, token=None, user_agent=DEFAULT_USER_AGENT,
                 ignore_bots=True, base_delay=1.0, max_delay=60.0, help_command=True,
                 channels=None, require_channels=False, cursor_path=None, default_data_path=None):
        self.prefix = prefix
        self._url = url
        self._token = token
        self.user_agent = user_agent
        self.ignore_bots = ignore_bots
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.channels = set(channels) if channels else None
        self.require_channels = require_channels
        self.cursor_path = cursor_path
        self.default_data_path = default_data_path
        self.data_path = os.environ.get("SLIMM_DB_PATH") or default_data_path
        self._setting_errors = []
        self.commands = {}
        self._unique_commands = []
        self._listeners = {}
        self._global_checks = []
        self.client = None
        self.space = None
        self.authors = None
        self.me_id = None
        self._cursor_conn = None
        self._background_tasks = set()
        self._fatal_error = None
        self._main_task = None
        self._gateway = None
        if help_command:
            self._register_default_help()

    def check(self, func):
        """Registers an async predicate run before every command; a truthy string return refuses with that reply."""
        self._global_checks.append(func)
        return func

    def background(self, coro, *, name=None):
        """Runs `coro` as a supervised task: held strongly, cancelled on shutdown, and a real exception stops the bot."""
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_done)
        return task

    def _on_background_done(self, task):
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        err = task.exception()
        if err is None:
            return
        self._fatal_error = err
        label = task.get_name() or "background task"
        if is_token_revoked(err):
            print(f"{label}: token rejected - revoked?", file=sys.stderr)
        else:
            print(f"{label} failed: {type(err).__name__}: {err}", file=sys.stderr)
        if self._main_task is not None:
            self._main_task.cancel()

    def setting(self, name, default=None, *, type=str, required=False):
        """One config value from the environment, converted by `type`; a missing `required` one is reported at `start()`."""
        raw = os.environ.get(name)
        if not raw:
            if required:
                self._setting_errors.append(name)
            return default
        if type is int:
            return int(raw)
        if type is float:
            return float(raw)
        if type is list:
            return [item.strip() for item in raw.split(",") if item.strip()]
        return raw

    def command(self, name=None, *, aliases=(), help=None, usage=None, cooldown=None, requires=None, check=None):
        def decorator(func):
            self.add_command(Command(
                func, name=name or func.__name__, aliases=aliases, help=help,
                usage=usage, cooldown=cooldown, requires=requires, check=check,
            ))
            return func
        return decorator

    def group(self, name=None, *, aliases=(), help=None, cooldown=None, requires=None, check=None):
        def decorator(func):
            grp = Group(
                func, name=name or func.__name__, aliases=aliases, help=help,
                cooldown=cooldown, requires=requires, check=check,
            )
            self.add_command(grp)
            return grp
        return decorator

    def add_command(self, command):
        for name in command.names:
            if name in self.commands:
                raise ValueError(f"command name/alias `{name}` is already registered")
            self.commands[name] = command
        self._unique_commands.append(command)

    @property
    def channel(self):
        """The one configured channel, when `channels` names exactly one; None otherwise."""
        if self.channels and len(self.channels) == 1:
            return next(iter(self.channels))
        return None

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
        await guard_dispatch(self._dispatch_event, "on_frame", frame)
        if kind == "message.created":
            channel_id = frame.get("channel_id")
            if self.channels is not None and channel_id not in self.channels:
                return
            message = frame.get("message") or {}
            self._note_seq(channel_id, message.get("seq"))
            await guard_dispatch(self._dispatch_event, "on_raw_message", message)
            await guard_dispatch(self.process_message, message)
            return
        global_event = _GLOBAL_EVENT_FRAMES.get(kind)
        if global_event:
            await self._dispatch_typed(*global_event, frame)
            return
        channel_event = _CHANNEL_EVENT_FRAMES.get(kind)
        if channel_event:
            if self.channels is not None and frame.get("channel_id") not in self.channels:
                return
            await self._dispatch_typed(*channel_event, frame)

    async def _dispatch_typed(self, name, payload_cls, frame):
        """Wraps `frame` in `payload_cls` unless it is `None` (the pre-typed events keep the raw frame dict)."""
        payload = payload_cls(frame) if payload_cls else frame
        await guard_dispatch(self._dispatch_event, name, payload)

    def _note_seq(self, channel_id, seq):
        if self._cursor_conn is not None and seq is not None:
            cursor.set(self._cursor_conn, channel_id, seq)

    async def _catch_up(self):
        """Replays any `/sync` backlog for `channels` through `process_message`, persisting the cursor as it goes."""
        if not self.channels or self._cursor_conn is None:
            return
        for channel_id in self.channels:
            await catchup.bootstrap(self.client, self._cursor_conn, channel_id)
        scopes = [{"channel_id": c, "after_seq": cursor.get(self._cursor_conn, c)} for c in self.channels]
        for scope in await catchup.sync(self.client, scopes):
            channel_id = scope["channel_id"]
            for message in scope["messages"]:
                await guard_dispatch(self.process_message, message)
            if scope["messages"]:
                cursor.set(self._cursor_conn, channel_id, scope["messages"][-1]["seq"])
            elif scope["reset"]:
                await catchup.bootstrap(self.client, self._cursor_conn, channel_id)

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
        await self._catch_up()

        async with await Gateway.open(self.client) as gateway:
            self._gateway = gateway
            try:
                reset_delay()
                await self._dispatch_event("on_ready")
                async for frame in gateway.frames():
                    await self._handle_frame(frame)
            finally:
                self._gateway = None

    async def send_frame(self, frame):
        """Sends a client->server frame (typing, canvas.cursor, canvas.stroke_preview) over the open gateway."""
        if self._gateway is None:
            raise RuntimeError("not connected - send_frame needs an open gateway")
        await self._gateway.send(frame)

    async def start_typing(self, channel_id):
        """One typing refresh; slim-m has no explicit stop frame, it lapses on its own after a few seconds."""
        await self.send_frame({"type": "typing", "channel_id": channel_id})

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

    def _resolve_channels_from_env(self):
        raw = os.environ.get("SLIMM_CHANNELS", "")
        return {c.strip() for c in raw.split(",") if c.strip()}

    def _resolve_config(self, url, token):
        """`(base, token)`, or a `RuntimeError` naming every missing piece of config at once."""
        base = url or self._url or os.environ.get("SLIMM_URL", "")
        token = token or self._token or os.environ.get("SLIMM_BOT_TOKEN", "")
        if self.channels is None:
            env_channels = self._resolve_channels_from_env()
            if env_channels:
                self.channels = env_channels

        missing = []
        if not base:
            missing.append("SLIMM_URL")
        if not token:
            missing.append("SLIMM_BOT_TOKEN")
        if self.require_channels and not self.channels:
            missing.append("SLIMM_CHANNELS")
        missing.extend(self._setting_errors)
        if missing:
            raise RuntimeError(f"set {', '.join(missing)}")
        return base, token

    def _open_cursor_if_scoped(self):
        if not self.channels:
            return
        path = self.cursor_path or self.data_path or os.environ.get("SLIMM_CURSOR_DB") or DEFAULT_CURSOR_DB
        self._cursor_conn = sqlite3.connect(path, isolation_level=None)
        cursor.init_table(self._cursor_conn)

    async def start(self, *, url=None, token=None):
        base, token = self._resolve_config(url, token)
        self.client = AsyncClient(base, token, self.user_agent)
        self.space = Space(self.client)
        self.authors = AuthorFilter(self.client, space=self.space, ignore_bots=self.ignore_bots)
        self._open_cursor_if_scoped()
        self._main_task = asyncio.create_task(self._run_forever())
        try:
            return await run_with_shutdown(self._main_task)
        finally:
            for task in self._background_tasks:
                task.cancel()
            if self._background_tasks:
                await asyncio.gather(*self._background_tasks, return_exceptions=True)
            await self.client.aclose()
            if self._cursor_conn is not None:
                self._cursor_conn.close()

    def run(self, *, url=None, token=None):
        """A cancelled `start()` (SIGTERM, or a fatal background task) becomes 0 for a clean shutdown, 1 otherwise."""
        try:
            return asyncio.run(self.start(url=url, token=token)) or 0
        except asyncio.CancelledError:
            return 1 if self._fatal_error is not None else 0
