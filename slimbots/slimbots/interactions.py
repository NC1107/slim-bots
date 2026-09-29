"""A member pressing one of this bot's buttons (slim-m decision 0039), and how `@bot.button` finds its handler."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from . import components
from .lifecycle import guard_dispatch

if TYPE_CHECKING:
    from .bot import Bot

ButtonHandler = Callable[["Interaction"], Awaitable[Any]]


class Interaction:
    """One press. Answer within 15 minutes with `reply_ephemeral`, `edit_components` or `ack`."""

    def __init__(self, frame: dict[str, Any], bot: Bot) -> None:
        self.id = frame["interaction_id"]
        self.channel_id = frame["channel_id"]
        self.message_id = frame["message_id"]
        self.custom_id = frame["custom_id"]
        self.user_id = frame["user_id"]
        self.user_display_name = frame.get("user_display_name")
        self.created_at = frame.get("created_at")
        self.bot = bot
        self.answered = False

    async def reply_ephemeral(self, content: str) -> Any:
        """Answers only the member who pressed; the server allows three of these per press."""
        assert self.bot.client is not None
        result = await self.bot.client.send_ephemeral_to_press(self.channel_id, self.id, content)
        self.answered = True
        return result

    async def edit_components(self, layout: components.Rows) -> Any:
        """Replaces the buttons on the pressed message (`[]` clears them) and answers this press."""
        assert self.bot.client is not None
        result = await self.bot.client.edit_components(
            self.channel_id, self.message_id, layout, interaction_id=self.id,
        )
        self.answered = True
        return result

    async def ack(self) -> None:
        """Says the press was seen and needs no visible answer."""
        assert self.bot.client is not None
        await self.bot.client.ack_interaction(self.channel_id, self.id)
        self.answered = True


class ButtonRoutes:
    """`@bot.button("hit")` for one custom_id, `@bot.button(prefix="vote:")` for a family of them."""

    def __init__(self) -> None:
        self._routes: list[tuple[str | None, str | None, ButtonHandler]] = []

    def add(self, custom_id: str | None, prefix: str | None, func: ButtonHandler) -> None:
        if (custom_id is None) == (prefix is None):
            raise ValueError("button() takes a custom_id or a prefix, not both and not neither")
        self._routes.append((custom_id, prefix, func))

    def matching(self, custom_id: str) -> list[ButtonHandler]:
        return [
            f for exact, prefix, f in self._routes
            if exact == custom_id or (prefix is not None and custom_id.startswith(prefix))
        ]


async def dispatch_press(bot: Bot, routes: ButtonRoutes, frame: dict[str, Any]) -> None:
    """Runs `on_interaction` listeners and the matching handlers, then acks a press that no handler answered."""
    interaction = Interaction(frame, bot)
    await bot._dispatch_event("on_interaction", interaction)
    handlers = routes.matching(interaction.custom_id)
    failed = False

    async def report(err: Exception) -> None:
        nonlocal failed
        failed = True
        print(f"unhandled error in button `{interaction.custom_id}`: {err}", file=sys.stderr)

    for handler in handlers:
        await guard_dispatch(handler, interaction, on_error=report)
    # A handler that raised is left unanswered on purpose, so the member sees the press failed.
    if handlers and not failed and not interaction.answered:
        await guard_dispatch(interaction.ack)
