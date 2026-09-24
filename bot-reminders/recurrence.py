"""Recurring-reminder scheduling and per-user timezones.

A recurring reminder is never re-created on fire - the same row's `due_at`
is recomputed and the row kept, which is what lets `!reminders` and
`!reminders cancel` keep working on it exactly like a one-off. There are two
schedule kinds: `weekly` ("every monday at 09:00") and `interval` ("every
2h"). Kept out of `bot.py` so the scheduling math - and its one real
subtlety, DST-safe weekly recomputation via `zoneinfo` - does not crowd out
the command handling.

`zoneinfo` is standard library since Python 3.9; the IANA database itself is
not guaranteed to be installed on every platform (a minimal Debian image, in
particular), which is the one dependency this bot adds over the others:
`tzdata` on PyPI, a pure-data package Python's own `zoneinfo` falls back to
automatically when the OS has no timezone database of its own.
"""

import datetime as dt
from zoneinfo import ZoneInfo

WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
WEEKDAY_ALIASES = {
    "mon": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "wed": "wednesday",
    "weds": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}


def normalize_weekday(name):
    """A weekday name or common abbreviation -> `0` (Monday) through `6`
    (Sunday), or `None` if `name` is not one."""
    name = WEEKDAY_ALIASES.get(name.lower(), name.lower())
    return WEEKDAY_NAMES.get(name)


def is_valid_timezone(name):
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def local_clock_time(now_epoch, hour, minute, tz_name):
    """The next UTC epoch second `HH:MM` falls on in `tz_name`, today or
    tomorrow if that time has already passed today."""
    tz = ZoneInfo(tz_name)
    now = dt.datetime.fromtimestamp(now_epoch, tz)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    return int(candidate.timestamp())


def next_weekly(reference_epoch, weekday, hour, minute, tz_name):
    """The next occurrence of `weekday` (Monday=0) at `HH:MM` in `tz_name`,
    strictly after `reference_epoch`. Passing a reminder's own last `due_at`
    back in as `reference_epoch` is exactly what advances it by one week -
    the candidate always equals `reference_epoch` in that case, and "equal
    counts as already passed" pushes it forward exactly seven days.
    """
    tz = ZoneInfo(tz_name)
    reference = dt.datetime.fromtimestamp(reference_epoch, tz)
    days_ahead = (weekday - reference.weekday()) % 7
    candidate = (reference + dt.timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= reference:
        candidate += dt.timedelta(days=7)
    return int(candidate.timestamp())


def next_interval(last_due_at, interval_seconds, now_epoch):
    """The next multiple of `interval_seconds` after `last_due_at` that
    still lies after `now_epoch` - so a bot that was offline through several
    intervals catches up to the present in one jump on reconnect instead of
    firing a burst of overdue reminders."""
    next_at = last_due_at + interval_seconds
    if next_at <= now_epoch:
        missed = (now_epoch - next_at) // interval_seconds + 1
        next_at += missed * interval_seconds
    return next_at


def format_local(epoch_seconds, tz_name):
    tz = ZoneInfo(tz_name)
    return dt.datetime.fromtimestamp(epoch_seconds, tz).strftime("%Y-%m-%d %H:%M %Z")
