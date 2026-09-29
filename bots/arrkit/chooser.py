"""The button chooser: one message, only its invoker may press, expires on its own; one `Chooser` per bot."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from slimbots import ApiError, Button, rows

EXPIRES_SECONDS = 300
MAX_LABEL = 80
PER_ROW = 2


@dataclass
class Pick:
    """One open chooser: who may answer it, what it offers, and what choosing does."""

    invoker_id: str
    invoker_name: str
    channel_id: str
    items: list[dict[str, Any]]
    query: str
    on_choose: Callable[[dict[str, Any]], Awaitable[str]]
    note: str = ""
    message_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    def expired(self):
        return time.monotonic() - self.created_at > EXPIRES_SECONDS


def _clip(text):
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "~"


class Chooser:
    """Owns the open picks for one command; `prefix` namespaces its buttons, `command` is what a retry types."""

    def __init__(self, prefix, command, label, verb):
        self.prefix = prefix
        self.command = command
        self.label = label
        self.verb = verb
        self._picks: dict[str, Pick] = {}

    def register(self, bot):
        bot.button(prefix=self.prefix)(self.on_press)

    def render(self, pick):
        buttons = [Button(_clip(self.label(item)), f"{self.prefix}sel:{i}") for i, item in enumerate(pick.items)]
        layout = [buttons[i:i + PER_ROW] for i in range(0, len(buttons), PER_ROW)] + [[Button("Cancel", f"{self.prefix}cancel", style="danger")]]
        text = f'{len(pick.items)} match(es) for "{pick.query}"{pick.note} - {pick.invoker_name}, pick one to {self.verb}.'
        return text, rows(*layout)

    async def open(self, bot, pick, *, reply_to_id=None):
        text, layout = self.render(pick)
        message = await bot.client.send(pick.channel_id, text, reply_to_id=reply_to_id, components=layout)
        pick.message_id = message.id
        self._picks[message.id] = pick
        bot.background(self._expire(bot, pick), name=f"{self.prefix}pick-{message.id}")
        return pick

    async def _expire(self, bot, pick):
        await asyncio.sleep(EXPIRES_SECONDS)
        if self._picks.get(pick.message_id) is pick:
            await self._close(bot, pick, f"timed out - `{bot.prefix}{self.command}` again to retry.")

    async def _close(self, bot, pick, text):
        self._picks.pop(pick.message_id, None)
        with contextlib.suppress(ApiError):
            await bot.client.edit_message(pick.channel_id, pick.message_id, text)
            await bot.client.edit_components(pick.channel_id, pick.message_id, [])

    async def on_press(self, interaction):
        bot = interaction.bot
        retry = f"`{bot.prefix}{self.command}`"
        pick = self._picks.get(interaction.message_id)
        if pick is None or pick.expired():
            if pick is not None:
                await self._close(bot, pick, f"timed out - {retry} again to retry.")
            with contextlib.suppress(ApiError):
                await interaction.reply_ephemeral(f"that choice has expired - run {retry} again.")
            return
        if interaction.user_id != pick.invoker_id:
            with contextlib.suppress(ApiError):
                await interaction.reply_ephemeral(f"only {pick.invoker_name} can choose here - {retry} starts your own.")
            return
        await interaction.ack()
        action = interaction.custom_id[len(self.prefix):]
        if action == "cancel":
            await self._close(bot, pick, "cancelled.")
        elif action.startswith("sel:") and action[4:].isdigit() and int(action[4:]) < len(pick.items):
            self._picks.pop(pick.message_id, None)
            await self._close(bot, pick, await pick.on_choose(pick.items[int(action[4:])]))
