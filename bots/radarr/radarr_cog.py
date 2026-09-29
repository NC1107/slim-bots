"""bot-radarr's `!radarr` command: the shared library command with Radarr's queue and calendar wording."""

from arrkit.chooser import Chooser
from arrkit.guard import Guard
from arrkit.library import Library, Spec, label

import radarr_core as core


def queue_line(record):
    left = f", {record['timeleft']} left" if record.get("timeleft") else ""
    return f"- {label(record.get('movie') or {'title': record.get('title', 'unknown')})} - {record.get('status', 'unknown')}{left}"


def release_in_window(movie, start, end):
    """The release date that put a movie on the calendar: cinemas, digital or physical, whichever falls in the window."""
    dates = [(movie.get(k) or "")[:10] for k in ("inCinemas", "digitalRelease", "physicalRelease")]
    inside = [d for d in dates if d and start <= d <= end]
    return min(inside) if inside else min((d for d in dates if d), default="")


def calendar_line(movie, start, end):
    return f"{release_in_window(movie, start, end)} {label(movie)}" + (" - downloaded" if movie.get("hasFile") else "")


SPEC = Spec(
    name="radarr", noun="movie", queue_params={"includeMovie": "true"}, queue_line=queue_line, calendar_params={},
    calendar_line=calendar_line, calendar_what="upcoming releases", added_text="searching for a release now",
)
GUARD = Guard(core.SERVICE)
CHOOSER = Chooser("radarrpick:", "radarr add", lambda item: label(item), "add")
LIBRARY = Library(core, SPEC, GUARD, CHOOSER)


def setup(bot):
    LIBRARY.setup(bot)
