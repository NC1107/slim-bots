"""bot-sonarr's `!sonarr` command: the shared library command with Sonarr's queue and calendar wording."""

from arrkit.chooser import Chooser
from arrkit.guard import Guard
from arrkit.library import Library, Spec, label

import sonarr_core as core


def queue_line(record):
    episode, series = record.get("episode") or {}, record.get("series") or {}
    name = record.get("title", "unknown")
    if episode:
        name = f"{series.get('title', name)} {core.episode_label(episode.get('seasonNumber', 0), episode.get('episodeNumber', 0))}"
    left = f", {record['timeleft']} left" if record.get("timeleft") else ""
    return f"- {name} - {record.get('status', 'unknown')}{left}"


def calendar_line(item, _start, _end):
    when = (item.get("airDateUtc") or "")[:10]
    episode = core.episode_label(item.get("seasonNumber", 0), item.get("episodeNumber", 0))
    return f"{when} {(item.get('series') or {}).get('title', 'unknown')} {episode}" + (" - downloaded" if item.get("hasFile") else "")


SPEC = Spec(
    name="sonarr", noun="show", queue_params={"includeSeries": "true", "includeEpisode": "true"}, queue_line=queue_line,
    calendar_params={"includeSeries": "true"}, calendar_line=calendar_line, calendar_what="episodes airing",
    added_text="searching for episodes now",
)
GUARD = Guard(core.SERVICE)
CHOOSER = Chooser("sonarrpick:", "sonarr add", lambda item: label(item), "add")
LIBRARY = Library(core, SPEC, GUARD, CHOOSER)


def setup(bot):
    LIBRARY.setup(bot)
