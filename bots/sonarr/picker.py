"""The button chooser for `!sonarr add`: one message, only its invoker may press, expires on its own."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from slimbots import ApiError, Button, rows

ID_PREFIX = "sonarrpick:"
EXPIRES_SECONDS = 300
MAX_LABEL = 80

_picks: dict[str, "Pick"] = {}


@dataclass
class Pick:
    """One open chooser and who may answer it."""

    invoker_id: str
    invoker_name: str
    channel_id: str
    items: list[dict[str, Any]]
    query: str
    on_choose: Callable[[dict[str, Any]], Awaitable[str]]
    message_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    def expired(self):
        return time.monotonic() - self.created_at > EXPIRES_SECONDS


def label_for(item):
    year = f" ({item['year']})" if item.get("year") else ""
    text = f"{item.get('title') or 'Untitled'}{year}"
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "~"


def render(pick):
    buttons = [Button(label_for(item), f"{ID_PREFIX}sel:{i}") for i, item in enumerate(pick.items)]
    layout = [buttons[i:i + 4] for i in range(0, len(buttons), 4)] + [[Button("Cancel", f"{ID_PREFIX}cancel", style="danger")]]
    return f'{len(pick.items)} match(es) for "{pick.query}" - {pick.invoker_name}, pick one to add.', rows(*layout)


async def open_pick(bot, pick, *, reply_to_id=None):
    text, layout = render(pick)
    message = await bot.client.send(pick.channel_id, text, reply_to_id=reply_to_id, components=layout)
    pick.message_id = message.id
    _picks[message.id] = pick
    bot.background(_expire(bot, pick), name=f"sonarr-pick-{message.id}")
    return pick


async def _expire(bot, pick):
    await asyncio.sleep(EXPIRES_SECONDS)
    if _picks.get(pick.message_id) is pick:
        await _close(bot, pick, "timed out - `!sonarr add` again to retry.")


async def _close(bot, pick, text):
    _picks.pop(pick.message_id, None)
    with contextlib.suppress(ApiError):
        await bot.client.edit_message(pick.channel_id, pick.message_id, text)
        await bot.client.edit_components(pick.channel_id, pick.message_id, [])


async def on_pick_press(interaction):
    bot = interaction.bot
    pick = _picks.get(interaction.message_id)
    if pick is None or pick.expired():
        if pick is not None:
            await _close(bot, pick, "timed out - `!sonarr add` again to retry.")
        with contextlib.suppress(ApiError):
            await interaction.reply_ephemeral("that choice has expired - run `!sonarr add` again.")
        return
    if interaction.user_id != pick.invoker_id:
        with contextlib.suppress(ApiError):
            await interaction.reply_ephemeral(f"only {pick.invoker_name} can choose here - `!sonarr add` starts your own.")
        return
    await interaction.ack()
    action = interaction.custom_id[len(ID_PREFIX):]
    if action == "cancel":
        await _close(bot, pick, "cancelled.")
    elif action.startswith("sel:") and action[4:].isdigit() and int(action[4:]) < len(pick.items):
        _picks.pop(pick.message_id, None)
        result = await pick.on_choose(pick.items[int(action[4:])])
        await _close(bot, pick, result)
