"""What a command handler receives: who sent it, where, and how to answer."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bot import Bot
    from .commands import Command
    from .embeds import Embed
    from .models import Member

TYPING_REFRESH_SECONDS = 4  # comfortably under slim-m's 6-second typing TTL


class _Typing:
    """Keeps a channel's typing indicator alive for `async with ctx.typing():`'s whole body."""

    def __init__(self, bot: Bot, channel_id: str | None) -> None:
        self._bot = bot
        self._channel_id = channel_id
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> _Typing:
        await self._bot.start_typing(self._channel_id)
        self._task = asyncio.create_task(self._refresh_loop())
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        assert self._task is not None
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(TYPING_REFRESH_SECONDS)
            await self._bot.start_typing(self._channel_id)


class Context:
    """One invocation of one command."""

    def __init__(
        self, *, bot: Bot, message: dict[str, Any], author: Member, channel_id: str | None,
        command: Command | None = None, invoked_with: str | None = None, raw_args: str = "",
    ) -> None:
        self.bot = bot
        self.message = message
        self.author = author
        self.channel_id = channel_id
        self.command = command
        self.invoked_with = invoked_with
        self.raw_args = raw_args

    @property
    def channel(self) -> Any:
        assert self.bot.space is not None
        return self.bot.space.channels.get(self.channel_id or "")

    def _prepare(self, content: str | None, embed: Embed | None) -> tuple[str, list[dict[str, Any]] | None, str | None]:
        """Body content (never blank - slim-m refuses that) and the fallback text for a server that rejects `embeds`."""
        if embed is None:
            return content or "", None, None
        rendered = embed.render_fallback()
        body_content = content if content else rendered
        fallback = f"{content}\n{rendered}" if content else rendered
        return body_content, [embed.to_wire()], fallback

    async def send(self, content: str | None = None, *, embed: Embed | None = None, channel_id: str | None = None) -> Any:
        assert self.bot.client is not None
        target = channel_id or self.channel_id
        assert target is not None, "send() needs a channel_id - this message's own channel_id was missing"
        body_content, embeds, fallback = self._prepare(content, embed)
        return await self.bot.client.send(target, body_content, embeds=embeds, fallback_content=fallback)

    async def reply(self, content: str | None = None, *, embed: Embed | None = None) -> Any:
        assert self.bot.client is not None
        assert self.channel_id is not None, "reply() needs a channel_id - this message's own channel_id was missing"
        body_content, embeds, fallback = self._prepare(content, embed)
        return await self.bot.client.send(
            self.channel_id, body_content, reply_to_id=self.message.get("id"), embeds=embeds, fallback_content=fallback
        )

    def typing(self) -> _Typing:
        """`async with ctx.typing():` shows a typing indicator for the block's whole duration, not just one refresh."""
        return _Typing(self.bot, self.channel_id)

    async def confirm(self, prompt: str, *, timeout: float = 30) -> bool:
        """Asks a yes/no question and waits for this author's reply here; True/False, or False on a timeout."""
        await self.reply(f"{prompt} (yes/no)")

        def is_a_yes_or_no_reply(message: dict[str, Any]) -> bool:
            same_place = message.get("channel_id") == self.channel_id and message.get("author_id") == self.author.id
            return same_place and (message.get("content") or "").strip().lower() in ("yes", "no", "y", "n")

        try:
            message = await self.bot.wait_for("on_raw_message", check=is_a_yes_or_no_reply, timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return message.get("content", "").strip().lower() in ("yes", "y")
