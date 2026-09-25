"""`Bot`: the one constructor a script needs - config, auth, the websocket,
reconnect, command dispatch, and `run()`.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
import sys
from types import ModuleType
from typing import Any, Awaitable, Callable, Coroutine

from . import catchup, cursor
from . import events as ev
from .authors import AuthorFilter
from .canvas import Canvas
from .commands import Command, Group, build_help_text
from .context import Context
from .exceptions import CommandError, CommandNotFound
from .gateway import Gateway
from .http import ApiError, AsyncClient, is_forbidden, is_token_revoked
from .lifecycle import guard_dispatch, run_with_shutdown
from .models import Member
from .registration import register_commands
from .space import Space
from .store import Store
from .voice import Voice

DEFAULT_USER_AGENT = "slimbots/0.3"
DEFAULT_CURSOR_DB = "slimbots-cursor.db"

EventFrame = tuple[str, "type[Any] | None"]

# Deployment-wide (or DM/user-scoped) frame types: (handler name, payload class or None for the raw frame).
# member.joined is not here; see the on_member_join special case in _handle_frame below.
_GLOBAL_EVENT_FRAMES: dict[str, EventFrame] = {
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
_CHANNEL_EVENT_FRAMES: dict[str, EventFrame] = {
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

    def __init__(
        self, prefix: str = "!", *, url: str | None = None, token: str | None = None,
        user_agent: str = DEFAULT_USER_AGENT, ignore_bots: bool = True, base_delay: float = 1.0,
        max_delay: float = 60.0, help_command: bool = True, channels: set[str] | list[str] | None = None,
        require_channels: bool = False, cursor_path: str | None = None, default_data_path: str | None = None,
        store_migrate: Callable[[Any], None] | None = None,
    ) -> None:
        self.prefix = prefix
        self._url = url
        self._token = token
        self.user_agent = user_agent
        self.ignore_bots = ignore_bots
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.channels: set[str] | None = set(channels) if channels else None
        self.require_channels = require_channels
        self.cursor_path = cursor_path
        self.default_data_path = default_data_path
        self.data_path = os.environ.get("SLIMM_DB_PATH") or default_data_path
        self._store_migrate = store_migrate
        self._setting_errors: list[str] = []
        self.commands: dict[str, Command] = {}
        self._unique_commands: list[Command] = []
        self._listeners: dict[str, list[Callable[..., Awaitable[Any]]]] = {}
        self._global_checks: list[Callable[[Context], Awaitable[str | None]]] = []
        self.client: AsyncClient | None = None
        self.space: Space | None = None
        self.authors: AuthorFilter | None = None
        self.me_id: str | None = None
        self._cursor_conn: sqlite3.Connection | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._fatal_error: BaseException | None = None
        self._main_task: asyncio.Task[Any] | None = None
        self._gateway: Gateway | None = None
        self.store: Store | None = None
        self._extensions: dict[str, ModuleType] = {}
        self.voice = Voice(self)
        if help_command:
            self._register_default_help()

    def load_extension(self, module: str | ModuleType) -> ModuleType:
        """Imports `module` (a name, or an already-imported module) and calls its `setup(bot)`; see docs/framework.md."""
        name = module if isinstance(module, str) else module.__name__
        if name in self._extensions:
            return self._extensions[name]
        if isinstance(module, str):
            module = importlib.import_module(module)
        module.setup(self)  # type: ignore[attr-defined]
        self._extensions[name] = module
        return module

    def canvas(self, channel_id: str) -> Canvas:
        """A `Canvas` for `channel_id`, wired with this bot's client and gateway - never tied to a command's own channel."""
        assert self.client is not None, "canvas() needs an open connection"
        return Canvas(self.client, channel_id, bot=self)

    async def open_store(self, *, migrate: Callable[[Any], None] | None = None, path: str | None = None) -> Store:
        """Opens (or returns the already-open) thread-offloaded `Store` at `path` or `self.data_path`."""
        if self.store is None:
            resolved_path = path or self.data_path
            if resolved_path is None:
                raise RuntimeError("open_store needs a path - pass path=, or set default_data_path/SLIMM_DB_PATH")
            self.store = Store(resolved_path, migrate=migrate)
            await self.store.open()
        return self.store

    def check(self, func: Callable[[Context], Awaitable[str | None]]) -> Callable[[Context], Awaitable[str | None]]:
        """Registers an async predicate run before every command; a truthy string return refuses with that reply."""
        self._global_checks.append(func)
        return func

    def background(self, coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
        """Runs `coro` as a supervised task: held strongly, cancelled on shutdown, and a real exception stops the bot."""
        task = asyncio.create_task(coro, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_done)
        return task

    def _on_background_done(self, task: asyncio.Task[Any]) -> None:
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

    def setting(self, name: str, default: Any = None, *, type: type = str, required: bool = False) -> Any:
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

    def command(
        self, name: str | None = None, *, aliases: tuple[str, ...] = (), help: str | None = None, usage: str | None = None,
        cooldown: float | None = None, cooldown_bucket: str = "user", requires: str | None = None,
        check: Callable[[Context], Any] | None = None,
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            self.add_command(Command(
                func, name=name or func.__name__, aliases=aliases, help=help, usage=usage,
                cooldown=cooldown, cooldown_bucket=cooldown_bucket, requires=requires, check=check,
            ))
            return func
        return decorator

    def group(
        self, name: str | None = None, *, aliases: tuple[str, ...] = (), help: str | None = None,
        cooldown: float | None = None, cooldown_bucket: str = "user", requires: str | None = None,
        check: Callable[[Context], Any] | None = None,
    ) -> Callable[[Callable[..., Awaitable[Any]]], Group]:
        def decorator(func: Callable[..., Awaitable[Any]]) -> Group:
            grp = Group(
                func, name=name or func.__name__, aliases=aliases, help=help,
                cooldown=cooldown, cooldown_bucket=cooldown_bucket, requires=requires, check=check,
            )
            self.add_command(grp)
            return grp
        return decorator

    def add_command(self, command: Command) -> None:
        for name in command.names:
            if name in self.commands:
                raise ValueError(f"command name/alias `{name}` is already registered")
            self.commands[name] = command
        self._unique_commands.append(command)

    @property
    def channel(self) -> str | None:
        """The one configured channel, when `channels` names exactly one; None otherwise."""
        if self.channels and len(self.channels) == 1:
            return next(iter(self.channels))
        return None

    def get_command(self, name: str) -> Command | None:
        return self.commands.get(name)

    def unique_commands(self) -> list[Command]:
        return list(self._unique_commands)

    def event(self, func: Callable[..., Awaitable[Any]] | None = None, *, name: str | None = None) -> Any:
        def decorator(f: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            self._listeners.setdefault(name or f.__name__, []).append(f)
            return f
        return decorator(func) if func else decorator

    async def wait_for(
        self, event: str, *, check: Callable[..., bool] | None = None, timeout: float | None = None,
    ) -> Any:
        """Waits for the next `event` (an `on_*` name) where `check(*args)` is true; raises `asyncio.TimeoutError`."""
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        async def listener(*args: Any) -> None:
            if check is not None and not check(*args):
                return
            if not future.done():
                future.set_result(args[0] if len(args) == 1 else args)

        self._listeners.setdefault(event, []).append(listener)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._listeners[event].remove(listener)

    def _register_default_help(self) -> None:
        @self.command(name="help", help="Show this list, or one command's usage")
        async def help_command(ctx: Context, command_name: str | None = None) -> None:
            await ctx.reply(build_help_text(self, command_name=command_name))

    async def _dispatch_event(self, name: str, *args: Any) -> None:
        for handler in self._listeners.get(name, []):
            await guard_dispatch(handler, *args)

    async def _resolve_author(self, author_id: str) -> Member | None:
        assert self.space is not None, "_resolve_author needs an open connection"
        member = self.space.members.get(author_id)
        if member is not None:
            return member
        try:
            return await self.space.fetch_member(author_id)
        except ApiError:
            return None

    async def process_message(self, message: dict[str, Any]) -> None:
        """One `message.created` payload: the bot-ignore default, command
        parsing, argument conversion, and dispatch to the matched handler."""
        assert self.authors is not None, "process_message needs an open connection"
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
            if self._listeners.get("on_command_not_found"):
                await self._dispatch_not_found(message, author_id, invoked_with, raw_args)
            return

        author = await self._resolve_author(author_id)
        if author is None:
            return
        ctx = Context(
            bot=self, message=message, author=author, channel_id=message.get("channel_id"),
            command=command, invoked_with=invoked_with, raw_args=raw_args,
        )
        await self._invoke(ctx, command)

    async def _dispatch_not_found(self, message, author_id, invoked_with, raw_args):
        """Off by default: only resolves the author and builds a `ctx` when something is actually listening."""
        author = await self._resolve_author(author_id)
        if author is None:
            return
        ctx = Context(
            bot=self, message=message, author=author, channel_id=message.get("channel_id"),
            invoked_with=invoked_with, raw_args=raw_args,
        )
        await self._dispatch_event("on_command_not_found", ctx, CommandNotFound(invoked_with))

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

    async def _handle_frame(self, frame: dict[str, Any]) -> None:
        kind: str = frame.get("type") or ""
        await guard_dispatch(self._dispatch_event, "on_frame", frame)
        if kind == "message.created":
            channel_id = frame.get("channel_id")
            if self.channels is not None and channel_id not in self.channels:
                return
            message = frame.get("message") or {}
            self._note_seq(channel_id, message.get("seq"))
            await guard_dispatch(self._dispatch_event, "on_raw_message", message)
            self.background(guard_dispatch(self.process_message, message), name=f"message-{message.get('id', '?')}")
            return
        if kind == "voice.participant_joined":
            # Never gated on self.channels - find_member() tracks every voice channel the bot can see.
            self.voice._note_joined(frame["channel_id"], frame["user_id"])
            return
        if kind == "voice.participant_left":
            self.voice._note_left(frame["channel_id"], frame["user_id"])
            return
        if kind == "member.joined":
            # Special-cased, not a _GLOBAL_EVENT_FRAMES entry: on_member_join needs a resolved Member, which payload_cls(frame) cannot fetch.
            user_id = frame.get("user_id")
            member = await self._resolve_author(user_id) if user_id else None
            if member is not None:
                await guard_dispatch(self._dispatch_event, "on_member_join", member)
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

    async def _dispatch_typed(self, name: str, payload_cls: type[Any] | None, frame: dict[str, Any]) -> None:
        """Wraps `frame` in `payload_cls` unless it is `None` (the pre-typed events keep the raw frame dict)."""
        payload = payload_cls(frame) if payload_cls else frame
        await guard_dispatch(self._dispatch_event, name, payload)

    def _note_seq(self, channel_id: str | None, seq: int | None) -> None:
        if self._cursor_conn is not None and seq is not None and channel_id is not None:
            cursor.set(self._cursor_conn, channel_id, seq)

    async def _catch_up(self) -> None:
        """Replays any `/sync` backlog for `channels` through `process_message`, persisting the cursor as it goes."""
        assert self.client is not None, "_catch_up needs start() to have run"
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

    async def _connect_once(self, reset_delay: Callable[[], None]) -> None:
        assert self.client is not None and self.space is not None, "_connect_once needs start() to have run"
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
        if self._store_migrate is not None:
            await self.open_store(migrate=self._store_migrate)
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
            if self.store is not None:
                await self.store.close()

    def run(self, *, url=None, token=None):
        """A cancelled `start()` (SIGTERM, or a fatal background task) becomes 0 for a clean shutdown, 1 otherwise."""
        try:
            return asyncio.run(self.start(url=url, token=token)) or 0
        except asyncio.CancelledError:
            return 1 if self._fatal_error is not None else 0
