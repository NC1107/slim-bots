"""Button pickers for `!watch`: the matches, a series' seasons and episodes, and the resume-or-start-over question."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from slimbots import ApiError, Button, rows

import jellyfin_core
import playback_progress
from stream_session import format_hms

ID_PREFIX = "jfp:"
PAGE_SIZE = 10
PER_ROW = 5
MAX_MATCHES = 5
EXPIRES_SECONDS = 300
MAX_LABEL = 80

_picks: dict[str, "Pick"] = {}


@dataclass
class Pick:
    """One open chooser message and where the invoker is in it."""

    invoker_id: str
    invoker_name: str
    channel_id: str
    on_choose: Callable[[Any], Awaitable[None]]
    view: str = "results"
    items: list[dict[str, Any]] = field(default_factory=list)
    query: str = ""
    series: dict[str, Any] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    seasons: list[dict[str, Any]] = field(default_factory=list)
    page: int = 0
    resume_seconds: float = 0.0
    title: str = ""
    message_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    def expired(self):
        return time.monotonic() - self.created_at > EXPIRES_SECONDS


def _clip(text):
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "~"


def label_for(item):
    name = item.get("Name") or "Untitled"
    kind = item.get("Type")
    if kind == "Episode":
        season, number = item.get("ParentIndexNumber"), item.get("IndexNumber")
        prefix = f"S{season}E{number} " if season is not None and number is not None else ""
        series = f"{item['SeriesName']} - " if item.get("SeriesName") and not prefix else ""
        return _clip(f"{series}{prefix}{name}")
    year = f" ({item['ProductionYear']})" if item.get("ProductionYear") else ""
    return _clip(f"{name}{year}" + (" - series" if kind == "Series" else ""))


def _page_slice(pick):
    start = pick.page * PAGE_SIZE
    return start, pick.items[start:start + PAGE_SIZE]


def _item_rows(pick):
    start, shown = _page_slice(pick)
    buttons = [Button(label_for(item), f"{ID_PREFIX}sel:{start + i}") for i, item in enumerate(shown)]
    return [buttons[i:i + PER_ROW] for i in range(0, len(buttons), PER_ROW)]


def _nav_row(pick):
    nav = []
    if pick.view in ("seasons", "episodes"):
        nav.append(Button("Back", f"{ID_PREFIX}back"))
    if pick.page > 0:
        nav.append(Button("Previous", f"{ID_PREFIX}prev"))
    if (pick.page + 1) * PAGE_SIZE < len(pick.items):
        nav.append(Button("Next page", f"{ID_PREFIX}next"))
    nav.append(Button("Cancel", f"{ID_PREFIX}cancel", style="danger"))
    return nav


def render(pick):
    """The message text and button rows for wherever the picker is."""
    if pick.view == "resume":
        text = f"**{pick.title}** is at {format_hms(pick.resume_seconds)}."
        return text, rows([
            Button(f"Resume at {format_hms(pick.resume_seconds)}", f"{ID_PREFIX}resume", style="primary"),
            Button("Start over", f"{ID_PREFIX}restart"), Button("Cancel", f"{ID_PREFIX}cancel", style="danger"),
        ])
    heading = {
        "results": f'{len(pick.items)} matches for "{pick.query}"',
        "seasons": f"**{(pick.series or {}).get('Name', 'Series')}** - pick a season",
        "episodes": f"**{pick.title}** - pick an episode",
    }[pick.view]
    pages = f" (page {pick.page + 1} of {-(-len(pick.items) // PAGE_SIZE)})" if len(pick.items) > PAGE_SIZE else ""
    return f"{heading}{pages} - {pick.invoker_name} is choosing.", rows(*_item_rows(pick), _nav_row(pick))


async def open_pick(bot, pick, *, reply_to_id=None):
    """Posts the chooser and remembers it, expiring after `EXPIRES_SECONDS`."""
    text, layout = render(pick)
    message = await bot.client.send(pick.channel_id, text, reply_to_id=reply_to_id, components=layout)
    pick.message_id = message.id
    _picks[message.id] = pick
    bot.background(_expire(bot, pick, EXPIRES_SECONDS), name=f"jellyfin-pick-{message.id}")
    return pick


async def _expire(bot, pick, delay):
    await asyncio.sleep(delay)
    if _picks.get(pick.message_id) is pick:
        await _close(bot, pick, "timed out - `!watch` again to retry.")


async def _close(bot, pick, text):
    _picks.pop(pick.message_id, None)
    with contextlib.suppress(ApiError):
        await bot.client.edit_message(pick.channel_id, pick.message_id, text)
        await bot.client.edit_components(pick.channel_id, pick.message_id, [])


async def _redraw(bot, pick):
    text, layout = render(pick)
    with contextlib.suppress(ApiError):
        await bot.client.edit_message(pick.channel_id, pick.message_id, text)
        await bot.client.edit_components(pick.channel_id, pick.message_id, layout)


async def _open_series(pick, series):
    user_id = await asyncio.to_thread(playback_progress.resolve_user_id)
    seasons = await asyncio.to_thread(jellyfin_core.series_seasons, series["Id"], user_id)
    pick.series, pick.title, pick.page, pick.seasons = series, series.get("Name") or "Series", 0, seasons
    if len(seasons) == 1:
        await _open_season(pick, seasons[0])
    else:
        pick.view, pick.items = "seasons", seasons


async def _open_season(pick, season):
    user_id = await asyncio.to_thread(playback_progress.resolve_user_id)
    pick.items = await asyncio.to_thread(jellyfin_core.season_episodes, pick.series["Id"], season["Id"], user_id)
    pick.view, pick.page = "episodes", 0
    pick.title = f"{(pick.series or {}).get('Name', 'Series')}, {season.get('Name') or 'season'}"


async def _select(bot, pick, index):
    item = pick.items[index] if 0 <= index < len(pick.items) else None
    if item is None:
        return
    if pick.view == "results" and item.get("Type") == "Series":
        pick.results = pick.items
        await _open_series(pick, item)
    elif pick.view == "seasons":
        await _open_season(pick, item)
    else:
        await _close(bot, pick, f"starting **{item.get('Name')}**...")
        await pick.on_choose(item)
        return
    await _redraw(bot, pick)


async def _back(bot, pick):
    """Episodes go back to the seasons (or the matches, for a one-season series), seasons to the matches."""
    if pick.view == "episodes" and len(pick.seasons) > 1:
        pick.view, pick.items = "seasons", pick.seasons
    else:
        pick.view, pick.items = "results", pick.results
    pick.page = 0
    await _redraw(bot, pick)


async def on_pick_press(interaction):
    bot = interaction.bot
    pick = _picks.get(interaction.message_id)
    if pick is None or pick.expired():
        if pick is not None:
            await _close(bot, pick, "timed out - `!watch` again to retry.")
        with contextlib.suppress(ApiError):
            await interaction.reply_ephemeral("that choice has expired - `!watch` again.")
        return
    if interaction.user_id != pick.invoker_id:
        with contextlib.suppress(ApiError):
            await interaction.reply_ephemeral(f"only {pick.invoker_name} can choose here - `!watch` starts your own.")
        return
    await interaction.ack()
    action = interaction.custom_id[len(ID_PREFIX):]
    if action.startswith("sel:"):
        await _select(bot, pick, int(action[4:]))
    elif action in ("resume", "restart"):
        await _close(bot, pick, "starting...")
        await pick.on_choose(pick.resume_seconds if action == "resume" else 0.0)
    elif action == "cancel":
        await _close(bot, pick, "cancelled.")
    elif action == "back":
        await _back(bot, pick)
    elif action in ("prev", "next"):
        pick.page = max(0, pick.page + (1 if action == "next" else -1))
        await _redraw(bot, pick)
